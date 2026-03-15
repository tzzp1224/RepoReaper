import json
import os
import asyncio
from datetime import datetime

import pytest

from evaluation.analyze_eval_results import EvaluationAnalyzer
from evaluation.data_router import DataRoutingEngine
from evaluation.models import (
    AgenticMetrics,
    DataQualityTier,
    EvaluationResult,
    GenerationMetrics,
    QueryRewriteMetrics,
    RetrievalMetrics,
)


def _make_generation_metrics(
    score: float = 0.8,
    query: str = "How does this work?",
    context: str = "def foo():\n    return 1",
    answer: str = "A" * 160,
) -> GenerationMetrics:
    return GenerationMetrics(
        query=query,
        retrieved_context=context,
        generated_answer=answer,
        faithfulness=score,
        answer_relevance=score,
        answer_completeness=score,
        code_correctness=1.0,
    )


def _line_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def test_data_quality_tier_single_source_routing(tmp_path):
    router = DataRoutingEngine(output_dir=str(tmp_path))

    result = EvaluationResult(
        session_id="s1",
        query="q1",
        repo_url="https://github.com/a/b",
        timestamp=datetime.now(),
        generation_metrics=_make_generation_metrics(score=0.8),
    )
    # 模拟外部路径手动注入分数与错误 tier
    result.overall_score = 0.65
    result.data_quality_tier = DataQualityTier.GOLD

    tier = router.route_sample(result)

    assert tier == DataQualityTier.BRONZE.value
    assert result.data_quality_tier == DataQualityTier.BRONZE
    assert result.sft_ready is False
    assert _line_count(router.positive_samples_file) == 0
    assert _line_count(router.negative_samples_file) == 1


def test_data_quality_tier_boundaries_are_canonical():
    assert DataQualityTier.from_score(0.9) == DataQualityTier.GOLD
    assert DataQualityTier.from_score(0.7) == DataQualityTier.SILVER
    assert DataQualityTier.from_score(0.5) == DataQualityTier.BRONZE
    assert DataQualityTier.from_score(0.49) == DataQualityTier.REJECTED
    assert DataQualityTier.min_score_for(DataQualityTier.SILVER) == 0.7


def test_data_router_statistics_no_dpo_placeholder(tmp_path):
    router = DataRoutingEngine(output_dir=str(tmp_path))
    stats = router.get_statistics()
    assert set(stats.keys()) == {"positive", "negative"}


def test_evaluation_result_to_dict_includes_layer_overall_scores():
    query_rewrite = QueryRewriteMetrics(
        original_query="auth flow",
        rewritten_query="authentication login middleware",
        language_detected="en",
        keyword_coverage=0.8,
        semantic_preservation=0.9,
        diversity_score=0.7,
    )
    retrieval = RetrievalMetrics(
        query="auth flow",
        top_k=5,
        hit_rate=1.0,
        recall_at_k=0.8,
        precision_at_k=0.6,
        mrr=1.0,
        context_relevance=0.6,
        chunk_integrity=0.8,
        retrieval_latency_ms=120.0,
        vector_score_avg=0.7,
        bm25_score_avg=0.3,
    )
    generation = _make_generation_metrics(score=0.85)
    agentic = AgenticMetrics(
        query="auth flow",
        tool_selection_accuracy=1.0,
        tool_parameter_correctness=1.0,
        steps_taken=3,
        unnecessary_steps=0,
        backtrack_count=0,
        success=True,
    )

    result = EvaluationResult(
        session_id="s2",
        query="auth flow",
        repo_url="https://github.com/a/b",
        timestamp=datetime.now(),
        query_rewrite_metrics=query_rewrite,
        retrieval_metrics=retrieval,
        generation_metrics=generation,
        agentic_metrics=agentic,
    )
    result.compute_overall_score()
    payload = result.to_dict()

    assert payload["query_rewrite"]["overall_score"] == pytest.approx(query_rewrite.overall_score())
    assert payload["retrieval"]["overall_score"] == pytest.approx(retrieval.overall_score())
    assert payload["generation"]["overall_score"] == pytest.approx(generation.overall_score())
    assert payload["agentic"]["overall_score"] == pytest.approx(agentic.overall_score())
    assert "dpo_candidate" not in payload


def test_layer_performance_reads_serialized_layer_scores(tmp_path):
    result = EvaluationResult(
        session_id="s3",
        query="q3",
        repo_url="https://github.com/a/b",
        timestamp=datetime.now(),
        generation_metrics=_make_generation_metrics(score=0.9),
    )
    result.compute_overall_score()

    eval_file = tmp_path / "eval_results.jsonl"
    with open(eval_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    analyzer = EvaluationAnalyzer(eval_results_file=str(eval_file))
    layer_stats = analyzer.layer_performance()

    assert "generation" in layer_stats
    assert layer_stats["generation"]["avg"] == pytest.approx(
        result.generation_metrics.overall_score()
    )


def test_auto_eval_respects_enabled_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "dummy")

    import app.services.auto_evaluation_service as auto_eval_module
    from app.services.auto_evaluation_service import AutoEvaluationService, EvaluationConfig

    monkeypatch.setattr(auto_eval_module.tracing_service, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_eval_module.tracing_service, "record_score", lambda *args, **kwargs: None)

    class FakeEvalEngine:
        called = 0

        async def evaluate_generation(self, **kwargs):
            self.called += 1
            return _make_generation_metrics()

    fake_engine = FakeEvalEngine()
    router = DataRoutingEngine(output_dir=str(tmp_path / "sft"))
    service = AutoEvaluationService(
        eval_engine=fake_engine,
        data_router=router,
        config=EvaluationConfig(
            enabled=False,
            async_evaluation=False,
            min_query_length=1,
            min_answer_length=1,
            require_repo_url=False,
            require_code_in_context=False,
        ),
    )

    out = asyncio.run(
        service.auto_evaluate(
            query="q",
            retrieved_context="def x(): pass",
            generated_answer="answer long enough",
            session_id="sid",
            repo_url="https://github.com/a/b",
        )
    )
    assert out is None
    assert fake_engine.called == 0
    assert not os.path.exists(router.eval_results_file)


def test_needs_review_is_routed_only_after_approve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "dummy")

    import app.services.auto_evaluation_service as auto_eval_module
    from app.services.auto_evaluation_service import AutoEvaluationService, EvaluationConfig

    monkeypatch.setattr(auto_eval_module.tracing_service, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_eval_module.tracing_service, "record_score", lambda *args, **kwargs: None)

    class FakeEvalEngine:
        async def evaluate_generation(self, **kwargs):
            return _make_generation_metrics(
                score=0.82,
                query=kwargs["query"],
                context=kwargs["retrieved_context"],
                answer=kwargs["generated_answer"],
            )

    class TestService(AutoEvaluationService):
        async def _ragas_eval(self, query, context, answer):
            return 0.1, "mock_ragas"

    router = DataRoutingEngine(output_dir=str(tmp_path / "sft"))
    service = TestService(
        eval_engine=FakeEvalEngine(),
        data_router=router,
        config=EvaluationConfig(
            enabled=True,
            use_ragas=True,
            ragas_sample_rate=1.0,
            diff_threshold=0.2,
            async_evaluation=False,
            min_query_length=1,
            min_answer_length=1,
            require_repo_url=False,
            require_code_in_context=False,
        ),
    )

    tier = asyncio.run(
        service.auto_evaluate(
            query="how does it work",
            retrieved_context="def x(): pass",
            generated_answer="A" * 180,
            session_id="sid-1",
            repo_url="https://github.com/a/b",
        )
    )
    assert tier in {"gold", "silver", "bronze"}
    assert len(service.get_review_queue()) == 1

    before = _line_count(router.eval_results_file)
    service.approve_sample(0)
    after = _line_count(router.eval_results_file)

    assert len(service.get_review_queue()) == 0
    assert before == 0
    assert after == 1


def test_needs_review_reject_does_not_route(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "dummy")

    import app.services.auto_evaluation_service as auto_eval_module
    from app.services.auto_evaluation_service import AutoEvaluationService, EvaluationConfig

    monkeypatch.setattr(auto_eval_module.tracing_service, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_eval_module.tracing_service, "record_score", lambda *args, **kwargs: None)

    class FakeEvalEngine:
        async def evaluate_generation(self, **kwargs):
            return _make_generation_metrics(
                score=0.82,
                query=kwargs["query"],
                context=kwargs["retrieved_context"],
                answer=kwargs["generated_answer"],
            )

    class TestService(AutoEvaluationService):
        async def _ragas_eval(self, query, context, answer):
            return 0.1, "mock_ragas"

    router = DataRoutingEngine(output_dir=str(tmp_path / "sft"))
    service = TestService(
        eval_engine=FakeEvalEngine(),
        data_router=router,
        config=EvaluationConfig(
            enabled=True,
            use_ragas=True,
            ragas_sample_rate=1.0,
            diff_threshold=0.2,
            async_evaluation=False,
            min_query_length=1,
            min_answer_length=1,
            require_repo_url=False,
            require_code_in_context=False,
        ),
    )

    asyncio.run(
        service.auto_evaluate(
            query="how does it work",
            retrieved_context="def x(): pass",
            generated_answer="A" * 180,
            session_id="sid-1",
            repo_url="https://github.com/a/b",
        )
    )
    assert len(service.get_review_queue()) == 1

    before = _line_count(router.eval_results_file)
    service.reject_sample(0)
    after = _line_count(router.eval_results_file)

    assert before == 0
    assert after == 0
    assert len(service.get_review_queue()) == 0


def test_retrieval_script_drift_fixed():
    with open("evaluation/test_retrieval.py", "r", encoding="utf-8") as f:
        content = f.read()

    assert "file_list = await get_repo_structure(repo_url)" in content
    assert "store.collection.count()" not in content
