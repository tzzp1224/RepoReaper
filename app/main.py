# 文件路径: app/main.py
import sys
import io
import os # 新增
import time
import shutil
import asyncio
from contextlib import asynccontextmanager
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import StreamingResponse, HTMLResponse # 新增 HTMLResponse
from fastapi.staticfiles import StaticFiles # 新增 StaticFiles
import uvicorn

from app.core.config import settings
from app.services.agent_service import agent_stream
from app.services.chat_service import process_chat_stream
from app.services.vector_service import DATA_DIR, CHROMA_DIR, CONTEXT_DIR

settings.validate()

# === 新增：后台清理任务 ===
async def cleanup_cron_job():
    """
    后台任务：每小时运行一次。
    删除 data/ 目录下超过 24 小时的 Context JSON 和 ChromaDB 文件夹。
    """
    while True:
        try:
            print("🧹 [System] Starting scheduled data cleanup...")
            now = time.time()
            cutoff = 24 * 3600  # 24小时 (秒)
            
            # 1. 清理 JSON Context 文件
            if os.path.exists(CONTEXT_DIR):
                for filename in os.listdir(CONTEXT_DIR):
                    filepath = os.path.join(CONTEXT_DIR, filename)
                    # 检查最后修改时间
                    if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > cutoff:
                        os.remove(filepath)
                        print(f"   - Deleted old context: {filename}")

            # 2. 清理 ChromaDB (注意：Chroma 生成的是文件夹或 sqlite3 文件)
            # 警告：直接删除 Chroma 文件比较暴力，但在无状态设计下是安全的。
            # 如果是 sqlite3 文件模式：
            if os.path.exists(CHROMA_DIR):
                 # Chroma 通常在 CHROMA_DIR 下生成 chroma.sqlite3 或 uuid 文件夹
                 # 这里我们只清理整个 collection 相关的，比较复杂。
                 # 简单策略：如果整个项目文件夹是临时的，可以遍历 session 相关的。
                 # 但由于 Chroma 是单库多 Collection 结构，物理删除比较难。
                 # 替代方案：依靠 vector_service 中的 reset_collection 逻辑即可。
                 # 或者：如果你想彻底重置，可以定期清理整个 chromadb 目录（慎用，会清空所有 session）。
                 pass 
                 # 修正建议：对于 Demo 项目，主要占用空间的是 Context JSON。
                 # Chroma 的 SQLite 文件如果增长过大，建议直接重启服务时清空。
            
        except Exception as e:
            print(f"⚠️ Cleanup Task Error: {e}")
        
        await asyncio.sleep(3600) # 等待 1 小时

# === 使用 lifespan 管理生命周期 ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时运行
    task = asyncio.create_task(cleanup_cron_job())
    yield
    # 关闭时运行 (可选：取消任务)
    task.cancel()

app = FastAPI(title="GitHub RAG Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"],
)

# === 核心修改：托管静态文件与前端 ===

# 1. 挂载 index.html 所在的目录 (假设 index.html 在 app/ 目录下)
# 如果 index.html 在根目录，请把 directory 改为 "."
app.mount("/static", StaticFiles(directory="app"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    # 读取并返回 index.html
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health_check():
    return {"status": "ok"}

# ... (/analyze 和 /chat 路由保持不变) ...
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
    
    if not user_query: return {"answer": "请输入问题"}
    if not session_id: return {"answer": "Session 丢失"}

    return StreamingResponse(
        process_chat_stream(user_query, session_id), 
        media_type="text/plain"
    )

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False) # 生产模式建议关掉 reload