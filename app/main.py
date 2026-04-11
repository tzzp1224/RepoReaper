# 文件路径: app/main.py
import sys
import io
import os
import asyncio
from contextlib import asynccontextmanager

# 强制 stdout 使用 utf-8，防止 Windows 控制台乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.core.config import settings, auto_eval_config as runtime_auto_eval_config
from app.services.agent_service import agent_stream
from app.services.chat_service import process_chat_stream, get_eval_data, clear_eval_data
from app.services.insights_service import issue_summary_stream, commit_roadmap_stream
from app.services.vector_service import store_manager
from app.services.auto_evaluation_service import (
    init_auto_evaluation_service,
    get_auto_evaluation_service,
)
from app.services.tracing_service import tracing_service
from evaluation.evaluation_framework import EvaluationEngine, EvaluationResult, DataRoutingEngine
from datetime import datetime

settings.validate()

# === 生命周期管理 ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from app.services.vector_service import store_manager
    
    # 启动时运行
    print("🚀 Application starting...")
    # 仓库数据永久存储，对话记忆纯内存存储（重启自动清空）
    
    yield
    
    # 关闭时运行
    print("🛑 Application shutting down...")
    
    # 清理 GitHub 客户端连接
    from app.utils.github_client import close_github_client
    await close_github_client()
    
    # 清理向量存储连接
    await store_manager.close_all()

    # 关闭自动评估后台 worker
    auto_eval_service = get_auto_evaluation_service()
    if auto_eval_service:
        await auto_eval_service.shutdown()

    # 刷新并关闭 tracing 客户端（fail-open）
    tracing_service.shutdown()
    
    # 关闭共享的 Qdrant 客户端
    from app.storage.qdrant_store import close_shared_client
    await close_shared_client()
    
    print("✅ Cleanup complete")

app = FastAPI(title="GitHub RAG Agent", lifespan=lifespan)

# === 初始化评估引擎 ===
from app.utils.llm_client import client
eval_engine = EvaluationEngine(llm_client=client, model_name=settings.default_model_name)
data_router = DataRoutingEngine()

# === 初始化自动评估服务 (配置解耦到 app.core.config) ===
auto_eval_service = init_auto_evaluation_service(
    eval_engine=eval_engine,
    data_router=data_router,
    config=runtime_auto_eval_config
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

# Vue 3 构建输出的静态资源 (JS/CSS/assets)
import os
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend-dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="vue-assets")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    # 优先使用 Vue 3 构建版本，否则回退到原版
    vue_index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(vue_index):
        with open(vue_index, "r", encoding="utf-8") as f:
            return f.read()
    # 回退到原版前端
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/sessions")
async def get_sessions():
    """获取 session 管理状态"""
    return JSONResponse(store_manager.get_stats())

@app.post("/api/sessions/cleanup")
async def trigger_cleanup():
    """手动触发过期文件清理"""
    stats = await store_manager.cleanup_expired_files()
    return JSONResponse({"message": "Cleanup completed", "stats": stats})

@app.delete("/api/sessions/{session_id}")
async def close_session(session_id: str):
    """关闭指定 session"""
    await store_manager.close_session(session_id)
    return JSONResponse({"message": f"Session {session_id} closed"})


# === 仓库级 Session API ===

@app.post("/api/repo/check")
async def check_repo_session(request: Request):
    """
    检查仓库是否已有指定语言的索引和报告
    
    请求: { "url": "https://github.com/owner/repo", "language": "zh" }
    响应: { 
        "exists": true/false, 
        "session_id": "repo_xxx",
        "report": "..." (如果存在对应语言的报告),
        "has_index": true/false,
        "available_languages": ["en", "zh"]
    }
    """
    from app.utils.session import generate_repo_session_id
    
    data = await request.json()
    repo_url = data.get("url", "").strip()
    language = data.get("language", "en")
    
    if not repo_url:
        return JSONResponse({"error": "Missing URL"}, status_code=400)
    
    # 生成基于仓库的 Session ID
    session_id = generate_repo_session_id(repo_url)
    
    # 检查是否存在
    store = store_manager.get_store(session_id)
    
    # 尝试加载上下文
    context = store.load_context()
    
    if context and context.get("repo_url"):
        # 存在已分析的仓库
        # 获取指定语言的报告
        report = store.get_report(language)
        available_languages = store.get_available_languages()
        global_context = context.get("global_context", {})
        has_index = bool(global_context.get("file_tree"))
        
        return JSONResponse({
            "exists": True,
            "session_id": session_id,
            "repo_url": context.get("repo_url"),
            "report": report,  # 指定语言的报告，可能为 None
            "has_index": has_index,
            "available_languages": available_languages,
            "requested_language": language,
        })
    else:
        return JSONResponse({
            "exists": False,
            "session_id": session_id,
            "has_index": False,
            "available_languages": [],
        })


@app.get("/analyze")
async def analyze(url: str, session_id: str, language: str = "en", regenerate_only: bool = False): 
    """
    仓库分析端点
    
    Args:
        url: 仓库 URL
        session_id: Session ID
        language: 报告语言 ("en" 或 "zh")
        regenerate_only: True 时跳过抓取/索引，直接使用已有索引生成新语言报告
    """
    if not session_id:
        return {"error": "Missing session_id"}
    return EventSourceResponse(agent_stream(url, session_id, language, regenerate_only))


# === Insights 端点: Issue 摘要 & Commit Roadmap ===

@app.get("/api/insights/issues")
async def insights_issues(url: str, session_id: str, language: str = "en"):
    """
    Issue 摘要端点 (SSE)

    抓取目标仓库 Issues，LLM 生成结构化笔记总结。
    """
    if not url:
        return {"error": "Missing url"}
    return EventSourceResponse(issue_summary_stream(url, session_id, language))


@app.get("/api/insights/commits")
async def insights_commits(url: str, session_id: str, language: str = "en"):
    """
    Commit Roadmap 端点 (SSE)

    遍历目标仓库最近 Commits，LLM 生成 Mermaid Timeline 及叙述。
    """
    if not url:
        return {"error": "Missing url"}
    return EventSourceResponse(commit_roadmap_stream(url, session_id, language))


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

    trace_id = tracing_service.start_trace(
        trace_name="chat_session",
        session_id=session_id,
        metadata={
            "repo_url": repo_url,
            "query_preview": user_query[:200],
        },
    )

    # 标记流是否完成
    stream_completed = False
    
    async def chat_stream_with_eval():
        """包装 process_chat_stream，流结束后触发评估"""
        nonlocal stream_completed
        # 清除旧的评估数据
        clear_eval_data(session_id)

        try:
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

                    # 异步执行评估（sidecar 队列，不阻塞主链路）
                    await auto_eval_service.auto_evaluate_async(
                        query=user_query,
                        retrieved_context=eval_data.retrieved_context,
                        generated_answer=eval_data.answer,
                        session_id=session_id,
                        repo_url=repo_url,
                        language="zh" if any('\u4e00' <= c <= '\u9fff' for c in user_query) else "en"
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
        finally:
            tracing_service.end_trace(
                {
                    "session_id": session_id,
                    "stream_completed": stream_completed,
                    "trace_id": trace_id,
                }
            )
    
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
                    "sample_id": item.get("sample_id"),
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

        ok, message = auto_eval_service.approve_sample(index)
        return {
            "status": "success" if ok else "failed",
            "message": message,
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

        ok, message = auto_eval_service.reject_sample(index)
        return {
            "status": "success" if ok else "failed",
            "message": message,
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/auto-eval/approve-by-id/{sample_id}")
async def approve_sample_by_id(sample_id: str):
    """
    人工批准某个样本（稳定 sample_id，幂等）

    POST /auto-eval/approve-by-id/sample_xxx
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}

        ok, message = auto_eval_service.approve_sample_by_id(sample_id)
        return {
            "status": "success" if ok else "failed",
            "sample_id": sample_id,
            "message": message,
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.post("/auto-eval/reject-by-id/{sample_id}")
async def reject_sample_by_id(sample_id: str):
    """
    人工拒绝某个样本（稳定 sample_id，幂等）

    POST /auto-eval/reject-by-id/sample_xxx
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}

        ok, message = auto_eval_service.reject_sample_by_id(sample_id)
        return {
            "status": "success" if ok else "failed",
            "sample_id": sample_id,
            "message": message,
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
                "diff_threshold": auto_eval_service.config.diff_threshold,
                "visualize_only": auto_eval_service.config.visualize_only,
            },
            "runtime": auto_eval_service.get_runtime_status(),
            "review_queue_size": len(queue),
            "last_update": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


@app.get("/auto-eval/metrics")
async def auto_eval_metrics():
    """
    获取自动评估运行时指标（可观测专用）

    GET /auto-eval/metrics
    """
    try:
        auto_eval_service = get_auto_evaluation_service()
        if not auto_eval_service:
            return {"error": "Auto evaluation service not initialized", "status": "failed"}

        return {
            "status": "success",
            "metrics": auto_eval_service.get_metrics(),
            "last_update": datetime.now().isoformat(),
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
