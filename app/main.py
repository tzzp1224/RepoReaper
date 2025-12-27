# 文件路径: app/main.py
import sys
import io

# ✅ [关键] 解决 Windows 终端中文乱码问题
# 必须放在任何 print 操作之前
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import uvicorn

# 引入我们重构后的模块
from app.core.config import settings
from app.services.agent_service import agent_stream
from app.services.vector_service import vector_db
from app.utils.llm_client import client

# 启动前校验
settings.validate()

app = FastAPI(title="GitHub RAG Agent")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {
        "status": "ok", 
        "model": settings.MODEL_NAME,
        "api_key_set": bool(settings.GEMINI_API_KEY)
    }

# SSE 流式分析接口
@app.get("/analyze")
async def analyze(url: str):
    return EventSourceResponse(agent_stream(url))

# RAG 聊天接口
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_query = data.get("query")
    
    if not user_query:
        return {"answer": "请输入问题"}

    # 1. 检索 (Retrieval)
    relevant_docs = vector_db.search(user_query, top_k=3)
    
    context_str = ""
    sources = []
    for doc in relevant_docs:
        context_str += f"\n--- 引用自 {doc['file']} ---\n{doc['content'][:1000]}...\n"
        sources.append(doc['file'])

    # 2. 生成 (Generation)
    if not client:
        return {"answer": "LLM Client 初始化失败，无法回答。"}

    prompt = f"""
    你是一个精通代码的 AI 助手。
    基于以下检索到的代码片段，回答用户问题。
    如果片段中没有答案，请诚实告知。
    
    === Context ===
    {context_str}

    === User Question ===
    {user_query}
    """

    try:
        response = client.models.generate_content(
            model=settings.MODEL_NAME,
            contents=prompt
        )
        return {"answer": response.text, "sources": list(set(sources))}
    except Exception as e:
        return {"answer": f"生成回答时出错: {str(e)}"}

if __name__ == "__main__":
    # 使用 Python 模块方式启动时的入口
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)