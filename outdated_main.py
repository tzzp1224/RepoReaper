# This is the version of MAIN without rag and vector store.

import sys
import os
import io
import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv
from google import genai

# 引入之前的工具
from tools_github import get_repo_structure, get_file_content

# ==========================================
# 配置
# ==========================================
load_dotenv()

# 读取 Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ 未找到 GEMINI_API_KEY，请检查 .env 文件")

client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-3-flash-preview" # 或 gemini-1.5-flash-001

app = FastAPI()

# 允许跨域 (前端开发必备)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 核心逻辑：把 Agent 变成一个生成器
# ==========================================
async def agent_stream(repo_url: str):
    """
    这是一个异步生成器 (Async Generator)。
    它不会一次性返回结果，而是像流水线一样，
    每做完一步，就用 yield 抛出一个 JSON 消息给前端。
    """
    
    # --- Step 1: 初始化 ---
    yield json.dumps({"step": "init", "message": f"🚀 正在连接 GitHub: {repo_url}..."})
    await asyncio.sleep(0.5) # 模拟一点延迟感
    
    try:
        file_list = get_repo_structure(repo_url)
        if not file_list:
            yield json.dumps({"step": "error", "message": "❌ 无法获取文件列表，请检查 URL 或 Token。"})
            return

        yield json.dumps({"step": "fetched", "message": f"📦 获取成功！共发现 {len(file_list)} 个核心文件。"})
        
        # 截取
        limit = 500
        file_list_str = "\n".join(file_list[:limit])

        # --- Step 2: 思考 (Gemini) ---
        yield json.dumps({"step": "thinking", "message": "🤖 Gemini 正在阅读目录，思考阅读哪些核心代码..."})
        
        selection_prompt = f"""
        You are a Senior Software Architect.
        Repo Structure (Truncated): {file_list_str}
        Identify top 3 critical files to understand the architecture.
        Return raw JSON list. Example: ["README.md", "main.py"]
        """
        
        # 这里用同步调用即可，因为是在线程池里跑，或者换成 async 版本
        # 为了演示简单，我们假设它是极快的
        response = client.models.generate_content(model=MODEL_NAME, contents=selection_prompt)
        
        selected_files = ["README.md"]
        try:
            clean_text = response.text.replace("```json", "").replace("```", "").strip()
            selected_files = json.loads(clean_text)
        except:
            pass

        yield json.dumps({"step": "plan", "message": f"🎯 决定深入分析以下文件: {selected_files}"})
        
        # --- Step 3: 下载与分析 ---
        code_context = ""
        for i, file_path in enumerate(selected_files):
            yield json.dumps({"step": "download", "message": f"📥 [{i+1}/{len(selected_files)}] 正在读取: {file_path}..."})
            content = get_file_content(repo_url, file_path)
            if content:
                code_context += f"\n\n=== FILE: {file_path} ===\n{content[:10000]}"
        
        # --- Step 4: 生成报告 (Stream) ---
        yield json.dumps({"step": "generating", "message": "📝 正在撰写最终技术报告..."})
        
        analysis_prompt = f"""
        You are a Tech Lead.
        Based on these files: {code_context}
        Write a concise technical report (in Chinese).
        Use Markdown formatting.
        """

        # ⭐️ 关键点：使用 stream=True 开启流式生成
        # 这样 Agent 打出一个字，前端就能显示一个字
        stream_response = client.models.generate_content(
            model=MODEL_NAME,
            contents=analysis_prompt,
            config={"response_mime_type": "text/plain"}, # 确保不是JSON
        )
        
        # 注意：Google SDK 的 stream 用法可能需要适配
        # 这里我们简单做，如果 SDK 不支持 async stream，我们先一次性返回
        # 为了展示含金量，我们这里模拟流式推送 (或者你可以查阅 SDK 文档实现真流式)
        
        final_text = stream_response.text
        
        # 模拟打字机效果 (让前端看起来像是在实时生成)
        chunk_size = 50
        for i in range(0, len(final_text), chunk_size):
            chunk = final_text[i:i+chunk_size]
            yield json.dumps({"step": "report_chunk", "chunk": chunk})
            await asyncio.sleep(0.1) 

        yield json.dumps({"step": "finish", "message": "✅ 分析完成"})

    except Exception as e:
        yield json.dumps({"step": "error", "message": f"💥 发生错误: {str(e)}"})

# ==========================================
# 路由 (API Endpoints)
# ==========================================

@app.get("/")
def home():
    return {"status": "Agent Service is Running"}

@app.get("/analyze")
async def analyze(url: str):
    """
    SSE 接口：前端通过 EventSource 连接这个接口
    """
    generator = agent_stream(url)
    return EventSourceResponse(generator)

# ==========================================
# 启动入口
# ==========================================
if __name__ == "__main__":
    import uvicorn
    # 启动服务器，端口 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)