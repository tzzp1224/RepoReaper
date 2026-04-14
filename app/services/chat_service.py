# 文件路径: app/services/chat_service.py
import json
import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, AsyncGenerator, List, Set
from app.core.config import settings
from app.utils.llm_client import client
from app.services.vector_service import store_manager
from app.services.github_service import get_file_content
from app.services.chunking_service import UniversalChunker, ChunkingConfig
from app.services.tracing_service import tracing_service
from app.utils.session import get_conversation_memory, ConversationMemory


# ============================================================
# 配置类 - 解耦所有可调参数
# ============================================================

@dataclass
class ChatConfig:
    """Chat 服务配置 - 集中管理所有参数"""
    # JIT 动态加载配置
    max_jit_rounds: int = 2           # 最大 JIT 轮数
    max_files_per_round: int = 3      # 每轮最多加载文件数
    
    # LLM 配置
    temperature_thinking: float = 0.1  # 思考阶段温度
    temperature_final: float = 0.2     # 最终回答温度
    max_tokens: int = 4096             # 最大 token 数
    
    # 检索配置
    retrieval_top_k: int = 6          # RAG 检索 top-k
    context_max_chars: int = 2000     # 单文档最大字符数
    
    # 对话上下文配置
    max_history_turns: int = 6        # 保留最近 N 轮对话
    summary_threshold: int = 10       # 超过 N 轮开始压缩
    
    # 调试配置
    show_debug_info: bool = False     # 是否显示调试信息


# 全局配置实例
chat_config = ChatConfig()


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
    """
    处理聊天流 - 支持多轮 JIT 动态加载文件 + 对话上下文记忆
    
    流程:
    1. 获取对话记忆，构建上下文
    2. 初始检索 RAG 上下文
    3. LLM 思考并回答，可能请求文件
    4. 如果请求文件，加载后继续对话 (最多 max_jit_rounds 轮)
    5. 最终生成答案并保存到对话记忆
    """
    vector_db = store_manager.get_store(session_id)
    cfg = chat_config  # 使用全局配置
    
    # === 获取对话记忆 ===
    memory = get_conversation_memory(session_id)
    memory.add_user_message(user_query)  # 立即记录用户消息
    
    # 检查是否需要摘要压缩
    if memory.needs_summarization():
        yield "> 📝 *Compressing conversation history...*\n\n"
        await _compress_conversation_history(memory)
    
    # === 评估数据收集变量 ===
    collected_context = ""
    collected_response = ""
    collected_retrieval_latency = 0.0
    collected_generation_latency = 0.0
    
    # === JIT 状态跟踪 ===
    all_loaded_files: Set[str] = set()      # 所有已加载的文件
    all_failed_files: Set[str] = set()      # 所有失败的文件
    jit_round = 0                            # 当前 JIT 轮数
    
    # === 语言环境检测 ===
    use_chinese = is_chinese_query(user_query)
    
    # UI 提示语
    ui_msgs = _get_ui_messages(use_chinese)

    # === 步骤 0: 查询重写 ===
    search_query = await _rewrite_query(user_query)
    yield f"{ui_msgs['thinking']}`{search_query}`...\n\n"
    
    # === 步骤 1: 初始 RAG 检索 ===
    retrieval_start = time.time()
    relevant_docs = await vector_db.search_hybrid(search_query, top_k=cfg.retrieval_top_k)
    retrieval_latency_ms = (time.time() - retrieval_start) * 1000
    collected_retrieval_latency = retrieval_latency_ms
    tracing_service.add_event("retrieval_completed", {
        "latency_ms": retrieval_latency_ms,
        "documents_retrieved": len(relevant_docs) if relevant_docs else 0
    })
    
    rag_context = _build_context(relevant_docs, cfg.context_max_chars)
    collected_context = rag_context
    
    # === 步骤 2: 构建初始 Prompt ===
    global_context = vector_db.global_context or {}
    file_tree = global_context.get("file_tree", "(File tree not available.)")
    agent_summary = global_context.get("summary", "")
    
    # 获取对话历史上下文
    conversation_context = _build_conversation_context(memory)
    
    system_instruction = _build_system_prompt(
        file_tree=file_tree,
        agent_summary=agent_summary,
        rag_context=rag_context,
        use_chinese=use_chinese,
        is_final_round=False,
        conversation_context=conversation_context
    )
    
    augmented_user_query = f"""
    {user_query}
    
    (System Note: Priority 1: Answer using context. Priority 2: Use <tool_code> ONLY if critical info is missing.)
    """
    
    if not client: 
        yield "❌ LLM Error: Client not initialized"
        return

    # 初始化对话历史
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": augmented_user_query}
    ]

    try:
        generation_start = time.time()
        
        # === 多轮 JIT 循环 ===
        while jit_round <= cfg.max_jit_rounds:
            is_final_round = (jit_round == cfg.max_jit_rounds)
            
            # 如果是最终轮，更新系统提示禁用工具
            if is_final_round and jit_round > 0:
                # 更新系统消息，告知这是最后一轮
                messages[0] = {"role": "system", "content": _build_system_prompt(
                    file_tree=file_tree,
                    agent_summary=agent_summary,
                    rag_context=collected_context,
                    use_chinese=use_chinese,
                    is_final_round=True,
                    failed_files=list(all_failed_files)
                )}
            
            # LLM 流式生成
            stream = await client.chat.completions.create(
                model=settings.default_model_name,
                messages=messages,
                stream=True,
                temperature=cfg.temperature_final if is_final_round else cfg.temperature_thinking,
                max_tokens=cfg.max_tokens
            )
            
            buffer = ""
            round_response = ""
            requested_files: Set[str] = set()
            
            async for chunk in stream:
                content = chunk.choices[0].delta.content or ""
                if not content:
                    continue
                
                buffer += content
                round_response += content
                collected_response += content
                
                # 检测 tool_code 标签
                if "</tool_code>" in buffer:
                    matches = re.findall(r"<tool_code>\s*(.*?)\s*</tool_code>", buffer, re.DOTALL)
                    for f in matches:
                        clean_f = f.strip().replace("'", "").replace('"', "").replace("`", "")
                        # 过滤已加载和已失败的文件
                        if clean_f and clean_f not in all_loaded_files and clean_f not in all_failed_files:
                            requested_files.add(clean_f)
                    yield content
                    buffer = ""
                else:
                    yield content
            
            # 处理缓冲区残留
            if "</tool_code>" in buffer:
                matches = re.findall(r"<tool_code>\s*(.*?)\s*</tool_code>", buffer, re.DOTALL)
                for f in matches:
                    clean_f = f.strip().replace("'", "").replace('"', "").replace("`", "")
                    if clean_f and clean_f not in all_loaded_files and clean_f not in all_failed_files:
                        requested_files.add(clean_f)
            
            # === 判断是否需要继续 JIT ===
            if not requested_files or is_final_round:
                # 没有新文件请求，或已达最大轮数，结束循环
                break
            
            # === JIT 文件加载 ===
            jit_round += 1
            
            # 限制每轮文件数
            files_to_load = list(requested_files)[:cfg.max_files_per_round]
            file_list_str = ", ".join([f"`{f}`" for f in files_to_load])
            
            yield f"\n\n> 🔍 **[JIT Round {jit_round}/{cfg.max_jit_rounds}]** {ui_msgs['action_short']}{file_list_str}...\n\n"
            
            if not vector_db.repo_url:
                yield ui_msgs['error_url']
                break
            
            # 加载文件
            round_loaded_docs = []
            round_failed_files = []
            
            for file_path in files_to_load:
                if file_path in vector_db.indexed_files:
                    docs = vector_db.get_documents_by_file(file_path)
                    round_loaded_docs.extend(docs)
                    all_loaded_files.add(file_path)
                    yield f"> ✅ Loaded: `{file_path}`\n"
                else:
                    success = await _download_and_index(vector_db, file_path)
                    if success:
                        docs = vector_db.get_documents_by_file(file_path)
                        round_loaded_docs.extend(docs)
                        all_loaded_files.add(file_path)
                        yield f"> ✅ Downloaded: `{file_path}`\n"
                    else:
                        round_failed_files.append(file_path)
                        all_failed_files.add(file_path)
                        yield f"> ⚠️ Failed: `{file_path}`\n"
            
            # 构建后续消息
            if round_loaded_docs:
                new_context = _build_context(round_loaded_docs, cfg.context_max_chars)
                collected_context += f"\n\n[JIT Round {jit_round} Context]\n{new_context}"
            
            # 构建状态消息
            status_msg = _build_jit_status_message(
                loaded_count=len(round_loaded_docs),
                failed_files=round_failed_files,
                remaining_rounds=cfg.max_jit_rounds - jit_round,
                use_chinese=use_chinese
            )
            
            context_section = f"\n\n[New Code Context]\n{_build_context(round_loaded_docs, cfg.context_max_chars)}" if round_loaded_docs else ""
            
            # 更新对话历史，继续对话
            messages.append({"role": "assistant", "content": round_response})
            messages.append({"role": "user", "content": f"{status_msg}{context_section}\n\nPlease continue your analysis."})
            
            yield "\n\n"  # 分隔符
        
        # === 生成完成 ===
        generation_latency_ms = (time.time() - generation_start) * 1000
        collected_generation_latency = generation_latency_ms
        
        tracing_service.add_event("generation_completed", {
            "latency_ms": generation_latency_ms,
            "jit_rounds": jit_round,
            "files_loaded": len(all_loaded_files),
            "files_failed": len(all_failed_files)
        })
        
        # === 保存助手回复到对话记忆 ===
        memory.add_assistant_message(collected_response)
        
        # 存储评估数据
        _eval_data_store[session_id] = ChatResult(
            answer=collected_response,
            retrieved_context=collected_context,
            generation_latency_ms=collected_generation_latency,
            retrieval_latency_ms=collected_retrieval_latency
        )
        print(f"📦 [EvalData] Session {session_id}: {len(collected_context)} chars context, {len(collected_response)} chars answer, {jit_round} JIT rounds, {memory.get_turn_count()} turns")

    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        # 即使出错也保存部分回复
        if collected_response:
            memory.add_assistant_message(collected_response + f"\n\n[Error: {error_msg}]")
        tracing_service.add_event("generation_error", {
            "error": error_msg,
            "error_type": type(e).__name__,
            "jit_round": jit_round
        })
        yield f"\n\n❌ System Error: {error_msg}"


# ============================================================
# 辅助函数
# ============================================================

def _get_ui_messages(use_chinese: bool) -> Dict[str, str]:
    """获取 UI 消息（根据语言）"""
    if use_chinese:
        return {
            "thinking": "> 🧠 **思考中:** 正在检索相关代码: ",
            "action_short": "正在读取文件: ",
            "error_url": "> ⚠️ 错误: 仓库链接丢失。\n",
        }
    else:
        return {
            "thinking": "> 🧠 **Thinking:** Searching for code related to: ",
            "action_short": "Retrieving files: ",
            "error_url": "> ⚠️ Error: Repository URL lost.\n",
        }


def _build_system_prompt(
    file_tree: str,
    agent_summary: str,
    rag_context: str,
    use_chinese: bool,
    is_final_round: bool,
    failed_files: List[str] = None,
    conversation_context: str = ""
) -> str:
    """构建系统提示词"""
    lang_instruction = (
        "IMPORTANT: The user is asking in Chinese. You MUST reply in Simplified Chinese (简体中文)."
        if use_chinese else "Reply in English."
    )
    
    if is_final_round:
        tool_instruction = """
    [INSTRUCTIONS - FINAL ROUND]
    This is your FINAL response. You MUST provide a complete answer NOW.
    - DO NOT request any more files
    - DO NOT use <tool_code> tags
    - Synthesize all available context and give your best answer
    - If some files were not accessible, explain what information is missing and provide the best possible answer with what you have
    """
        if failed_files:
            tool_instruction += f"\n    Note: The following files could not be accessed: {', '.join(failed_files)}"
    else:
        tool_instruction = """
    [INSTRUCTIONS]
    1. **CHECK CONTEXT FIRST**: Look at the [Current Code Context]. Does it contain the answer?
    2. **IF YES**: Answer directly. DO NOT use tools.
    3. **IF NO**: Request missing files using tags: <tool_code>path/to/file</tool_code>
    """
    
    # 添加对话历史上下文
    conversation_section = ""
    if conversation_context:
        conversation_section = f"""
    [Previous Conversation]
    {conversation_context}
    """
    
    return f"""
    You are a Senior GitHub Repository Analyst.
    {lang_instruction}
    
    [Global Context - Repo Map]
    {file_tree}
    
    [Agent Analysis Summary]
    {agent_summary}
    {conversation_section}
    [Current Code Context (Retrieved)]
    {rag_context}
    {tool_instruction}
    """


def _build_conversation_context(memory: ConversationMemory) -> str:
    """
    构建对话历史上下文字符串
    
    只包含最近几轮对话的摘要，用于 system prompt
    """
    messages = memory.get_context_messages()
    
    if len(messages) <= 2:
        # 只有当前轮，不需要历史
        return ""
    
    # 排除最后一条（当前用户消息）
    history_messages = messages[:-1]
    
    if not history_messages:
        return ""
    
    context_parts = []
    for msg in history_messages[-6:]:  # 最多 6 条（3 轮）
        role = "User" if msg["role"] == "user" else "Assistant"
        # 截断过长的内容
        content = msg["content"][:500]
        if len(msg["content"]) > 500:
            content += "..."
        context_parts.append(f"{role}: {content}")
    
    return "\n".join(context_parts)


async def _compress_conversation_history(memory: ConversationMemory) -> None:
    """
    压缩对话历史 - 使用 LLM 生成摘要
    """
    messages_to_summarize = memory.get_messages_to_summarize()
    
    if not messages_to_summarize:
        return
    
    # 构建摘要请求
    conversation_text = "\n".join([
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:300]}"
        for m in messages_to_summarize
    ])
    
    prompt = f"""Summarize the following conversation in 2-3 sentences, focusing on:
1. What questions were asked
2. Key information discovered
3. Important conclusions

Conversation:
{conversation_text}

Summary (be concise):"""

    try:
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200
        )
        summary = response.choices[0].message.content.strip()
        
        # 保存摘要
        end_idx = len(memory._messages) - chat_config.max_history_turns * 2
        memory.set_summary(summary, end_idx)
        
        print(f"📝 Conversation compressed: {len(messages_to_summarize)} messages -> summary")
    except Exception as e:
        print(f"⚠️ Failed to compress conversation: {e}")


def _build_jit_status_message(
    loaded_count: int,
    failed_files: List[str],
    remaining_rounds: int,
    use_chinese: bool
) -> str:
    """构建 JIT 状态消息"""
    if use_chinese:
        if loaded_count > 0 and not failed_files:
            return f"系统通知: 成功加载 {loaded_count} 个文件。"
        elif loaded_count > 0 and failed_files:
            failed_list = ", ".join(failed_files)
            return f"系统通知: 加载了 {loaded_count} 个文件，但以下文件无法访问: {failed_list}。"
        else:
            failed_list = ", ".join(failed_files)
            if remaining_rounds > 0:
                return f"系统通知: 文件 ({failed_list}) 无法访问。你还有 {remaining_rounds} 次机会请求其他文件，或者基于现有上下文回答。"
            else:
                return f"系统通知: 文件 ({failed_list}) 无法访问。请基于现有上下文给出最佳回答。"
    else:
        if loaded_count > 0 and not failed_files:
            return f"System Notification: Successfully loaded {loaded_count} files."
        elif loaded_count > 0 and failed_files:
            failed_list = ", ".join(failed_files)
            return f"System Notification: Loaded {loaded_count} files, but the following could not be accessed: {failed_list}."
        else:
            failed_list = ", ".join(failed_files)
            if remaining_rounds > 0:
                return f"System Notification: Files ({failed_list}) could not be accessed. You have {remaining_rounds} more attempts to request other files, or answer based on available context."
            else:
                return f"System Notification: Files ({failed_list}) could not be accessed. Please provide the best possible answer based on existing context."

async def _download_and_index(vector_db, file_path):
    """下载并索引文件"""
    try:
        content = await get_file_content(vector_db.repo_url, file_path)
        if not content: return False
        
        chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
        if not chunks: 
            chunks = [{
                "content": content,
                "metadata": {
                    "file": file_path,
                    "type": "text",
                    "name": "root",
                    "class": "",
                    "start_line": 1,
                    "end_line": max(1, content.count('\n') + 1),
                }
            }]
            
        documents = [c["content"] for c in chunks]
        metadatas = []
        for c in chunks:
            meta = c["metadata"]
            metadatas.append({
                "file": meta["file"],
                "type": meta["type"],
                "name": meta.get("name", ""),
                "class": meta.get("class") or "",
                "start_line": meta.get("start_line"),
                "end_line": meta.get("end_line"),
            })
        await vector_db.add_documents(documents, metadatas)
        return True
    except Exception as e:
        print(f"Download Error: {e}")
        return False


def _build_context(docs: List[Dict], max_chars: int = 2000) -> str:
    """构建上下文字符串"""
    if not docs:
        return "(No relevant code snippets found yet)"
    
    context = ""
    for doc in docs:
        file_info = doc.get('file', 'unknown')
        metadata = doc.get('metadata', {})
        
        if 'class' in metadata and metadata['class']:
            file_info += f" (Class: {metadata['class']})"
        
        content = doc.get('content', '')[:max_chars]
        context += f"\n--- File: {file_info} ---\n{content}\n"
    
    return context
