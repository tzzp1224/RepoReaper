# 文件路径: app/services/chat_service.py
import json
import asyncio
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, AsyncGenerator
from app.core.config import settings
from app.utils.llm_client import client
from app.services.vector_service import store_manager
from app.services.github_service import get_file_content
from app.services.chunking_service import UniversalChunker, ChunkingConfig
from app.services.tracing_service import tracing_service


@dataclass
class ChatResult:
    """聊天结果 - 用于后续自动评估"""
    answer: str                    # 最终回答
    retrieved_context: str        # 检索到的上下文
    generation_latency_ms: float  # 生成耗时
    retrieval_latency_ms: float = 0  # 检索耗时


# === 评估数据存储 (供 main.py 获取) ===
# 存储每个 session 的评估数据，key 为 session_id
_eval_data_store: Dict[str, ChatResult] = {}

def get_eval_data(session_id: str) -> Optional[ChatResult]:
    """获取指定 session 的评估数据"""
    return _eval_data_store.get(session_id)

def clear_eval_data(session_id: str) -> None:
    """清除指定 session 的评估数据"""
    if session_id in _eval_data_store:
        del _eval_data_store[session_id]


# [Fix 2] 使用 Config 对象初始化，而非直接传参
# 之前的写法: chunker = UniversalChunker(min_chunk_size=100)
# 现在的写法:
chunker = UniversalChunker(config=ChunkingConfig(min_chunk_size=100))

# === 新增：简单的中文检测 ===
def is_chinese_query(text: str) -> bool:
    """检测字符串中是否包含中文字符"""
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False

# === 优化 2：查询重写 (解决中英文检索不匹配问题) ===
async def _rewrite_query(user_query: str):
    """
    使用 LLM 将用户的自然语言（可能是中文）转换为 3-5 个代码搜索关键词（英文）。
    """
    prompt = f"""
    You are a Code Search Expert.
    Task: Convert the user's query into 3-5 English keywords for code search (BM25/Vector).
    
    User Query: "{user_query}"
    
    Rules:
    1. Output ONLY a JSON list of strings.
    2. Translate concepts to technical terms (e.g., "鉴权" -> "auth", "login", "middleware").
    3. Keep it short.
    
    Example Output: ["authentication", "login_handler", "jwt_verify"]
    """
    try:
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=100
        )
        content = response.choices[0].message.content
        # 简单清洗
        content = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        keywords = json.loads(content)
        if isinstance(keywords, list):
            return " ".join(keywords) # 返回空格分隔的字符串供 BM25 使用
        return user_query
    except Exception as e:
        print(f"⚠️ Query Rewrite Failed: {e}")
        return user_query # 降级：直接用原句

async def process_chat_stream(user_query: str, session_id: str):
    vector_db = store_manager.get_store(session_id)
    
    # === 评估数据收集变量 ===
    collected_context = ""  # 收集检索到的上下文
    collected_response = ""  # 收集完整响应
    collected_retrieval_latency = 0.0
    collected_generation_latency = 0.0
    
    # === 1. 语言环境检测 ===
    use_chinese = is_chinese_query(user_query)
    
    # 定义 UI 提示语 (根据语言切换)
    ui_msgs = {
        "thinking": f"> 🧠 **Thinking:** Searching for code related to: " if not use_chinese else f"> 🧠 **思考中:** 正在检索相关代码: ",
        "action": f"\n\n> 🔍 **Agent Action:** Retrieving missing files: " if not use_chinese else f"\n\n> 🔍 **Agent 动作:** 正在读取缺失文件: ",
        "error_url": f"> ⚠️ Error: Repository URL lost.\n" if not use_chinese else f"> ⚠️ 错误: 仓库链接丢失。\n",
        "warning_file": f"> ⚠️ Warning: Failed to access " if not use_chinese else f"> ⚠️ 警告: 无法读取 ",
        "system_note": "Please provide the FINAL answer." if not use_chinese else "System Notification: Files loaded. Please provide the FINAL answer in Chinese."
    }

    # === 步骤 0: 查询重写 (增强检索命中率) ===
    # 比如用户问 "鉴权在哪里？" -> rewrite -> "auth login verify"
    search_query = await _rewrite_query(user_query)
    # 可以在这里 yield 一个 debug 信息给前端，如果不想要可以注释掉
    yield f"{ui_msgs['thinking']}`{search_query}`...\n\n"
    
    # === 1. 检索 RAG (使用重写后的 Query) 并计时 ===
    retrieval_start = time.time()
    relevant_docs = await vector_db.search_hybrid(search_query, top_k=6)
    retrieval_latency_ms = (time.time() - retrieval_start) * 1000
    collected_retrieval_latency = retrieval_latency_ms  # 保存检索耗时
    tracing_service.add_event("retrieval_completed", {
        "latency_ms": retrieval_latency_ms,
        "documents_retrieved": len(relevant_docs) if relevant_docs else 0
    })
    
    rag_context = _build_context(relevant_docs)
    collected_context = rag_context  # 保存检索上下文
    
    # 2. 获取全局上下文
    global_context = vector_db.global_context or {}
    file_tree = global_context.get("file_tree", "(File tree not available.)")
    agent_summary = global_context.get("summary", "") 
    
    # 3. 构造 Prompt (Context Priority)
    lang_instruction = "IMPORTANT: The user is asking in Chinese. You MUST reply in Simplified Chinese (简体中文)." if use_chinese else "Reply in English."
    system_instruction = f"""
    You are a Senior GitHub Repository Analyst.
    {lang_instruction}
    
    [Global Context - Repo Map]
    {file_tree}
    
    [Agent Analysis Summary]
    {agent_summary}
    
    [Current Code Context (Retrieved)]
    {rag_context}
    
    [INSTRUCTIONS]
    1. **CHECK CONTEXT FIRST**: Look at the [Current Code Context]. Does it contain the answer?
    2. **IF YES**: Answer directly. DO NOT use tools.
    3. **IF NO**: Request missing files using tags.
    
    [Tool Usage]
    Format: <tool_code>path/to/file</tool_code>
    """
    
    augmented_user_query = f"""
    {user_query}
    
    (System Note: Priority 1: Answer using context. Priority 2: Use <tool_code> ONLY if critical info is missing.)
    """
    
    if not client: 
        yield "❌ LLM Error: Client not initialized"
        return

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": augmented_user_query}
    ]

    try:
        # === Phase 1: 思考与回答 ===
        generation_start = time.time()
        stream = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=messages,
            stream=True,
            temperature=0.1, 
            max_tokens=4096
        )
        
        buffer = ""
        full_response = ""
        requested_files = set()
        
        async for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            if not content: continue
            
            buffer += content
            full_response += content
            collected_response += content  # 收集完整响应
            
            # 检测标签
            if "</tool_code>" in buffer:
                matches = re.findall(r"<tool_code>\s*(.*?)\s*</tool_code>", buffer, re.DOTALL)
                for f in matches:
                    clean_f = f.strip().replace("'", "").replace('"', "").replace("`", "")
                    requested_files.add(clean_f)
                
                yield content
                buffer = "" 
            else:
                yield content

        if "</tool_code>" in buffer:
            matches = re.findall(r"<tool_code>\s*(.*?)\s*</tool_code>", buffer, re.DOTALL)
            for f in matches:
                clean_f = f.strip().replace("'", "").replace('"', "").replace("`", "")
                requested_files.add(clean_f)

        # === Phase 2: 按需下载 ===
        if requested_files:
            file_list_str = ", ".join([f"`{f}`" for f in requested_files])
            yield f"\n\n> 🔍 **Agent Action:** Retrieving missing files: {file_list_str}...\n\n"
            
            if not vector_db.repo_url:
                yield f"> ⚠️ Error: Repository URL lost.\n"
                return

            new_docs_accumulated = []
            for file_path in requested_files:
                if file_path in vector_db.indexed_files:
                    docs = vector_db.get_documents_by_file(file_path)
                    new_docs_accumulated.extend(docs)
                else:
                    success = await _download_and_index(vector_db, file_path)
                    if success:
                        docs = vector_db.get_documents_by_file(file_path)
                        new_docs_accumulated.extend(docs)
                    else:
                        yield f"> ⚠️ Warning: Failed to access `{file_path}`.\n"

            # === Phase 3: 最终回答 ===
            if new_docs_accumulated:
                supplementary_context = _build_context(new_docs_accumulated)
                
                final_messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": augmented_user_query},
                    {"role": "assistant", "content": full_response},
                    {"role": "user", "content": f"System Notification: Requested files loaded.\n\n[New Code Context]\n{supplementary_context}\n\nPlease provide the FINAL answer."}
                ]
                
                stream_final = await client.chat.completions.create(
                    model=settings.default_model_name,
                    messages=final_messages,
                    stream=True,
                    temperature=0.2
                )
                
                async for chunk in stream_final:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        collected_response += content  # 收集最终回答
                        yield content
        
        generation_latency_ms = (time.time() - generation_start) * 1000
        collected_generation_latency = generation_latency_ms
        tracing_service.add_event("generation_completed", {
            "latency_ms": generation_latency_ms,
            "token_count": len(full_response.split())
        })
        
        # === 存储评估数据供 main.py 获取 ===
        _eval_data_store[session_id] = ChatResult(
            answer=collected_response,
            retrieved_context=collected_context,
            generation_latency_ms=collected_generation_latency,
            retrieval_latency_ms=collected_retrieval_latency
        )
        print(f"📦 [EvalData] Stored for session {session_id}: context={len(collected_context)} chars, answer={len(collected_response)} chars")

    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        tracing_service.add_event("generation_error", {
            "error": error_msg,
            "error_type": type(e).__name__
        })
        yield f"❌ System Error: {error_msg}"

# 辅助函数保持不变
def _build_context(docs):
    if not docs: return "(No relevant code snippets found yet)"
    context = ""
    for doc in docs:
        file_info = doc['file']
        if 'class' in doc.get('metadata', {}):
            cls = doc['metadata']['class']
            if cls: file_info += f" (Class: {cls})"
        context += f"\n--- File: {file_info} ---\n{doc['content'][:2000]}\n"
    return context

async def _download_and_index(vector_db, file_path):
    try:
        content = await get_file_content(vector_db.repo_url, file_path)
        if not content: return False
        
        chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
        if not chunks: 
            chunks = [{
                "content": content,
                "metadata": {"file": file_path, "type": "text", "name": "root", "class": ""}
            }]
            
        documents = [c["content"] for c in chunks]
        metadatas = []
        for c in chunks:
            meta = c["metadata"]
            metadatas.append({
                "file": meta["file"],
                "type": meta["type"],
                "name": meta.get("name", ""),
                "class": meta.get("class") or ""
            })
        await vector_db.add_documents(documents, metadatas)
        return True
    except Exception as e:
        print(f"Download Error: {e}")
        return False