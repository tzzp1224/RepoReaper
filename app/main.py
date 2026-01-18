# 文件路径: app/main.py
import sys
import io
import os
import time
import shutil
import asyncio
from contextlib import asynccontextmanager

# 强制 stdout 使用 utf-8，防止 Windows 控制台乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.core.config import settings
from app.services.agent_service import agent_stream
from app.services.chat_service import process_chat_stream

from app.services.vector_service import vector_config, CHROMA_DIR, CONTEXT_DIR

settings.validate()

# === 后台清理任务 ===
async def cleanup_cron_job():
    """
    后台任务：每小时运行一次。
    删除 Context 目录下超过 24 小时的 JSON 文件。
    """
    while True:
        try:
            print(f"🧹 [System] Starting scheduled data cleanup in {vector_config.DATA_DIR}...")
            now = time.time()
            cutoff = 24 * 3600  # 24小时
            
            # 1. 清理 JSON Context 文件
            if os.path.exists(CONTEXT_DIR):
                for filename in os.listdir(CONTEXT_DIR):
                    filepath = os.path.join(CONTEXT_DIR, filename)
                    # 检查最后修改时间
                    if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > cutoff:
                        try:
                            os.remove(filepath)
                            print(f"   - Deleted old context: {filename}")
                        except OSError as e:
                            print(f"   - Error deleting {filename}: {e}")

            # 2. ChromaDB 清理策略 (仅占位，通常不建议暴力删除)
            if os.path.exists(CHROMA_DIR):
                 pass 
            
        except Exception as e:
            print(f"⚠️ Cleanup Task Error: {e}")
        
        await asyncio.sleep(3600) # 等待 1 小时

# === 生命周期管理 ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时运行
    task = asyncio.create_task(cleanup_cron_job())
    yield
    # 关闭时运行
    task.cancel()

app = FastAPI(title="GitHub RAG Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"],
)

# === 静态文件与前端 ===
app.mount("/static", StaticFiles(directory="app"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    # 确保 index.html 路径正确
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/analyze")
async def analyze(url: str, session_id: str, language: str = "en"): 
    if not session_id:
        return {"error": "Missing session_id"}
    return EventSourceResponse(agent_stream(url, session_id, language))

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_query = data.get("query")
    session_id = data.get("session_id")
    
    if not user_query: return {"answer": "Please enter your question"}
    if not session_id: return {"answer": "Session lost"}

    return StreamingResponse(
        process_chat_stream(user_query, session_id), 
        media_type="text/plain"
    )

if __name__ == "__main__":
    # 生产模式建议关掉 reload
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)