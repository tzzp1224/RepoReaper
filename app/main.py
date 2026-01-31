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
from app.services.chat_service import process_chat_stream, get_eval_data, clear_eval_data
from app.services.vector_service import vector_config, CHROMA_DIR, CONTEXT_DIR
from app.services.auto_evaluation_service import (
    init_auto_evaluation_service,
    get_auto_evaluation_service,
    EvaluationConfig
)
from evaluation.evaluation_framework import EvaluationEngine, EvaluationResult, DataRoutingEngine
from datetime import datetime
import uuid

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
    # 清理 GitHub 客户端连接
    from app.utils.github_client import close_github_client
    await close_github_client()

app = FastAPI(title="GitHub RAG Agent", lifespan=lifespan)

# === 初始化评估引擎 ===
from app.utils.llm_client import client
eval_engine = EvaluationEngine(llm_client=client, model_name=settings.default_model_name)
data_router = DataRoutingEngine()

# === 初始化自动评估服务 (Phase 1) ===
auto_eval_config = EvaluationConfig(
    enabled=True,
    use_ragas=False,              # Phase 1: 先不用 Ragas，避免额外依赖
    async_evaluation=True,        # 异步模式，不阻塞响应
    min_quality_score=0.4,        # 最低分数阈值（0.4 = 只拒绝最差的）
    min_query_length=10,          # 最小 query 长度
    min_answer_length=100,        # 最小 answer 长度
    require_repo_url=True,        # 必须有仓库 URL
    require_code_in_context=True  # 上下文必须包含代码
)
auto_eval_service = init_auto_evaluation_service(
    eval_engine=eval_engine,
    data_router=data_router,
    config=auto_eval_config
)
print("✅ Auto Evaluation Service Initialized")

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
    """
    聊天端点 - 自动评估版本
    
    改进点:
    1. 立即返回聊天结果（不阻塞）
    2. 后台异步进行自动评估
    3. 评估结果自动存储到 evaluation/sft_data/
    """
    data = await request.json()
    user_query = data.get("query")
    session_id = data.get("session_id")
    repo_url = data.get("repo_url", "")
    
    if not user_query:
        return {"answer": "Please enter your question"}
    if not session_id:
        return {"answer": "Session lost"}

    # 标记流是否完成
    stream_completed = False
    
    async def chat_stream_with_eval():
        """包装 process_chat_stream，流结束后触发评估"""
        nonlocal stream_completed
        
        # 清除旧的评估数据
        clear_eval_data(session_id)
        
        # 执行聊天流
        async for chunk in process_chat_stream(user_query, session_id):
            yield chunk
        
        # 流完成后标记
        stream_completed = True
        
        # 流结束后触发评估（此时数据已存储在 chat_service 中）
        try:
            auto_eval_service = get_auto_evaluation_service()
            eval_data = get_eval_data(session_id)
            
            if auto_eval_service and eval_data and eval_data.answer:
                print(f"\n📊 [Auto-Eval] Starting evaluation for session {session_id}")
                print(f"   - Query: {user_query[:50]}...")
                print(f"   - Context length: {len(eval_data.retrieved_context)} chars")
                print(f"   - Answer length: {len(eval_data.answer)} chars")
                
                # 异步执行评估（不阻塞流结束）
                asyncio.create_task(
                    auto_eval_service.auto_evaluate_async(
                        query=user_query,
                        retrieved_context=eval_data.retrieved_context,
                        generated_answer=eval_data.answer,
                        session_id=session_id,
                        repo_url=repo_url,
                        language="zh" if any('\u4e00' <= c <= '\u9fff' for c in user_query) else "en"
                    )
                )
            else:
                if not auto_eval_service:
                    print("⚠️ Auto evaluation service not initialized")
                elif not eval_data:
                    print(f"⚠️ No eval data found for session {session_id}")
                elif not eval_data.answer:
                    print(f"⚠️ Empty answer for session {session_id}")
        except Exception as e:
            print(f"⚠️ Failed to trigger auto-eval: {e}")
            import traceback
            traceback.print_exc()
    
    # 返回流
    return StreamingResponse(
        chat_stream_with_eval(),
        media_type="text/plain"
    )

# ===== Phase 2: 新增评估端点 =====

@app.post("/evaluate")
async def evaluate(request: Request):
    """
    评估端点: 接收生成结果,进行多维度评估
    
    POST /evaluate
    {
        "query": "用户问题",
        "retrieved_context": "检索到的文件内容",
        "generated_answer": "生成的回答",
        "session_id": "会话ID",
        "repo_url": "仓库URL（可选）"
    }
    """
    try:
        data = await request.json()
        
        # 提取必需字段
        query = data.get("query")
        retrieved_context = data.get("retrieved_context", "")
        generated_answer = data.get("generated_answer")
        session_id = data.get("session_id", "unknown")
        repo_url = data.get("repo_url", "")
        
        if not query or not generated_answer:
            return {
                "error": "Missing required fields: query, generated_answer",
                "status": "failed"
            }
        
        # 调用评估引擎获取生成层指标
        generation_metrics = await eval_engine.evaluate_generation(
            query=query,
            retrieved_context=retrieved_context,
            generated_answer=generated_answer
        )
        
        # 构建完整的评估结果对象
        evaluation_result = EvaluationResult(
            session_id=session_id,
            query=query,
            repo_url=repo_url,
            timestamp=datetime.now(),
            language="en",
            generation_metrics=generation_metrics
        )
        
        # 计算综合得分
        evaluation_result.compute_overall_score()
        
        # 数据路由: 根据得分将样本分类
        quality_tier = data_router.route_sample(evaluation_result)
        
        return {
            "status": "success",
            "evaluation": {
                "faithfulness": generation_metrics.faithfulness,
                "answer_relevance": generation_metrics.answer_relevance,
                "answer_completeness": generation_metrics.answer_completeness,
                "overall_score": evaluation_result.overall_score
            },
            "quality_tier": quality_tier,
            "session_id": session_id
        }
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "error": str(e),
            "status": "failed"
        }


# ===== 自动评估相关端点 =====

@app.get("/auto-eval/review-queue")
async def get_review_queue():
    """
    获取需要人工审查的样本列表
    
    这些是评估出现异常（自己的分数和Ragas分数差异过大）的样本
    需要人工判断哪个评估器更准确
    
    GET /auto-eval/review-queue
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}
        
        queue = auto_eval_service.get_review_queue()
        
        return {
            "status": "success",
            "queue_size": len(queue),
            "samples": [
                {
                    "index": i,
                    "query": item["eval_result"].query,
                    "custom_score": item["custom_score"],
                    "ragas_score": item["ragas_score"],
                    "diff": item["diff"],
                    "quality_tier": item["eval_result"].data_quality_tier.value,
                    "timestamp": item["timestamp"]
                }
                for i, item in enumerate(queue)
            ]
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/auto-eval/approve/{index}")
async def approve_sample(index: int):
    """
    人工批准某个样本（接受该评估结果）
    
    POST /auto-eval/approve/0
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}
        
        auto_eval_service.approve_sample(index)
        
        return {
            "status": "success",
            "message": f"Sample {index} approved and stored"
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/auto-eval/reject/{index}")
async def reject_sample(index: int):
    """
    人工拒绝某个样本（抛弃该评估结果）
    
    POST /auto-eval/reject/0
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}
        
        auto_eval_service.reject_sample(index)
        
        return {
            "status": "success",
            "message": f"Sample {index} rejected and removed from queue"
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.get("/auto-eval/stats")
async def auto_eval_stats():
    """
    获取自动评估统计信息
    
    GET /auto-eval/stats
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}
        
        queue = auto_eval_service.get_review_queue()
        
        return {
            "status": "success",
            "auto_evaluation": {
                "enabled": auto_eval_service.config.enabled,
                "use_ragas": auto_eval_service.config.use_ragas,
                "async_mode": auto_eval_service.config.async_evaluation,
                "custom_weight": auto_eval_service.config.custom_weight,
                "ragas_weight": auto_eval_service.config.ragas_weight,
                "diff_threshold": auto_eval_service.config.diff_threshold
            },
            "review_queue_size": len(queue),
            "last_update": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.get("/evaluation/stats")
async def evaluation_stats():
    """
    获取评估统计信息
    
    GET /evaluation/stats
    """
    try:
        stats = eval_engine.get_statistics()
        return {
            "status": "success",
            "statistics": {
                "total_evaluations": stats.get("total_evaluations", 0),
                "average_score": stats.get("average_score", 0),
                "quality_distribution": stats.get("quality_distribution", {}),
                "top_issues": stats.get("top_issues", [])
            }
        }
    except Exception as e:
        return {
            "error": str(e),
            "status": "failed"
        }


@app.get("/dashboard/quality-distribution")
async def quality_distribution():
    """
    获取数据质量分布 (用于仪表盘)
    
    GET /dashboard/quality-distribution
    """
    try:
        distribution = data_router.get_distribution()
        return {
            "status": "success",
            "distribution": {
                "gold": distribution.get("gold", 0),
                "silver": distribution.get("silver", 0),
                "bronze": distribution.get("bronze", 0),
                "rejected": distribution.get("rejected", 0),
                "corrected": distribution.get("corrected", 0)
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "error": str(e),
            "status": "failed"
        }


@app.get("/dashboard/bad-cases")
async def bad_cases():
    """
    获取低质量样本 (用于人工审核)
    
    GET /dashboard/bad-cases
    """
    try:
        bad_samples = data_router.get_bad_samples(limit=10)
        return {
            "status": "success",
            "bad_cases": [
                {
                    "query": s.get("query", ""),
                    "issue": s.get("issue", ""),
                    "score": s.get("score", 0)
                }
                for s in bad_samples
            ],
            "total_bad_cases": len(bad_samples)
        }
    except Exception as e:
        return {
            "error": str(e),
            "status": "failed"
        }


if __name__ == "__main__":
    # 生产模式建议关掉 reload
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)