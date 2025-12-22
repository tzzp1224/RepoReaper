import sys
import io
import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from google import genai
from dotenv import load_dotenv
import os

# 引入之前的工具
from tools_github import get_repo_structure, get_file_content
# 引入新写的向量库
from vector_store import VectorStore

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3-flash-preview"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🌎 全局唯一的向量数据库实例
# 注意：每次重启服务，内存数据库会清空，需要重新分析一次仓库
vector_db = VectorStore()

# ==========================================
# 1. 分析流程 (Indexing)
# ==========================================
async def agent_stream(repo_url: str):
    yield json.dumps({"step": "init", "message": f"🚀 正在连接 GitHub: {repo_url}..."})
    await asyncio.sleep(0.5)
    
    try:
        # 重置数据库 (避免混淆不同项目)
        # 简单起见，我们这里重新实例化一个，或者你可以写个 clear 方法
        global vector_db
        vector_db = VectorStore() 

        file_list = get_repo_structure(repo_url)
        if not file_list:
            yield json.dumps({"step": "error", "message": "❌ 无法获取文件列表。"})
            return

        yield json.dumps({"step": "fetched", "message": f"📦 获取成功！共发现 {len(file_list)} 个文件。"})
        
        # 截取
        limit = 400
        file_list_str = "\n".join(file_list[:limit])

        # Step 2: 思考
        yield json.dumps({"step": "thinking", "message": "🤖 Gemini 正在挑选核心代码..."})
        selection_prompt = f"""
        You are a Senior Software Architect.
        Repo Structure: {file_list_str}
        Identify top 3-5 critical files to understand the logic.
        Return raw JSON list. Example: ["README.md", "main.py"]
        """
        response = client.models.generate_content(model=MODEL_NAME, contents=selection_prompt)
        
        selected_files = ["README.md"]
        try:
            clean_text = response.text.replace("```json", "").replace("```", "").strip()
            selected_files = json.loads(clean_text)
        except:
            pass

        yield json.dumps({"step": "plan", "message": f"🎯 决定读取: {selected_files}"})
        
        # Step 3: 下载 + 建库 (Indexing)
        code_context = ""
        documents = []
        metadatas = []

        for i, file_path in enumerate(selected_files):
            yield json.dumps({"step": "download", "message": f"📥 [{i+1}/{len(selected_files)}] 读取并存入知识库: {file_path}..."})
            content = get_file_content(repo_url, file_path)
            if content:
                # 简单处理：把整个文件当做一个 chunk (实际 RAG 中会按字符切分)
                # 为了防止文件太大，我们截取前 8000 字符
                snippet = content[:8000]
                documents.append(snippet)
                metadatas.append({"file": file_path})
                
                # 拼接用于生成总结
                code_context += f"\n\n=== FILE: {file_path} ===\n{snippet}"
        
        # ⭐️ 核心动作：存入向量数据库
        yield json.dumps({"step": "indexing", "message": "🧠 正在构建 RAG 向量索引..."})
        vector_db.add_documents(documents, metadatas)

        # Step 4: 生成报告
        yield json.dumps({"step": "generating", "message": "📝 正在撰写分析报告..."})
        
        analysis_prompt = f"""
        You are a Tech Lead.
        Based on these files: {code_context}
        Write a concise technical report (in Chinese). Markdown format.
        """
        
        final_response = client.models.generate_content(
            model=MODEL_NAME, contents=analysis_prompt
        )
        
        # 模拟流式推送
        final_text = final_response.text
        chunk_size = 50
        for i in range(0, len(final_text), chunk_size):
            chunk = final_text[i:i+chunk_size]
            yield json.dumps({"step": "report_chunk", "chunk": chunk})
            await asyncio.sleep(0.05) 

        yield json.dumps({"step": "finish", "message": "✅ 分析完成！现在你可以向我提问了。"})

    except Exception as e:
        yield json.dumps({"step": "error", "message": f"💥 错误: {str(e)}"})

# ==========================================
# 2. 聊天接口 (Retrieval & Chat)
# ==========================================
class ChatRequest(json.JSONEncoder):
    # Pydantic 也可以，这里偷懒用 dict
    pass

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_query = data.get("query")
    
    if not user_query:
        return {"answer": "请输入问题"}

    print(f"User asked: {user_query}")

    # 1. 检索 (Retrieval)
    # 去向量库里找 3 个最相关的代码片段
    relevant_docs = vector_db.search(user_query, top_k=3)
    
    context_str = ""
    for doc in relevant_docs:
        context_str += f"\n--- 片段来自 {doc['file']} ---\n{doc['content'][:1000]}...\n"

    # 2. 增强生成 (Generation)
    prompt = f"""
    你是一个精通代码的 AI 助手。
    根据以下检索到的代码上下文 (Context)，回答用户的问题。
    如果上下文中没有答案，请诚实地说不知道，不要编造。

    === Context ===
    {context_str}

    === Question ===
    {user_query}
    """

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )
        return {"answer": response.text, "sources": [d['file'] for d in relevant_docs]}
    except Exception as e:
        return {"answer": f"抱歉，思考时出错了: {str(e)}"}

@app.get("/analyze")
async def analyze(url: str):
    return EventSourceResponse(agent_stream(url))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)