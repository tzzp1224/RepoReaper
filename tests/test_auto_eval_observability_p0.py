import asyncio
import os
import hashlib
import sys
import types
from datetime import datetime

from evaluation.data_router import DataRoutingEngine
from evaluation.models import GenerationMetrics


def _make_metrics(query: str, context: str, answer: str, score: float = 0.8) -> GenerationMetrics:
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


def _build_service(tmp_path, monkeypatch, config_kwargs=None, ragas_impl=None, sleep_seconds=0.0):
    monkeypatch.chdir(tmp_path)

    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "dummy")

    from app.core.config import AutoEvaluationConfig
    import app.services.auto_evaluation_service as auto_eval_module

    monkeypatch.setattr(auto_eval_module.tracing_service, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_eval_module.tracing_service, "record_score", lambda *args, **kwargs: None)

    class FakeEvalEngine:
        def __init__(self):
            self.called = 0

        async def evaluate_generation(self, **kwargs):
            self.called += 1
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            return _make_metrics(
                query=kwargs["query"],
                context=kwargs["retrieved_context"],
                answer=kwargs["generated_answer"],
            )

    class Service(auto_eval_module.AutoEvaluationService):
        pass

    if ragas_impl:
        Service._ragas_eval = ragas_impl

    cfg_defaults = dict(
        enabled=True,
        async_evaluation=False,
        use_ragas=False,
        min_query_length=1,
        min_answer_length=1,
        require_repo_url=False,
        require_code_in_context=False,
        visualize_only=True,
    )
    if config_kwargs:
        cfg_defaults.update(config_kwargs)

    config = AutoEvaluationConfig(**cfg_defaults)
    router = DataRoutingEngine(output_dir=str(tmp_path / "sft"))
    engine = FakeEvalEngine()
    service = Service(eval_engine=engine, data_router=router, config=config)
    return service, engine, router


def test_default_config_is_decoupled_from_service(monkeypatch):
    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "dummy")
    from app.core.config import AutoEvaluationConfig
    from app.services.auto_evaluation_service import EvaluationConfig

    assert EvaluationConfig is AutoEvaluationConfig


def test_visualize_only_does_not_write_sft_files(tmp_path, monkeypatch):
    service, engine, router = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={"visualize_only": True, "async_evaluation": False},
    )

    result = asyncio.run(
        service.auto_evaluate(
            query="explain auth flow",
            retrieved_context="def login(): pass",
            generated_answer="A" * 180,
            session_id="s1",
            repo_url="https://github.com/a/b",
        )
    )

    assert result in {"gold", "silver", "bronze", "rejected"}
    assert engine.called == 1
    assert _line_count(router.eval_results_file) == 0
    assert _line_count(router.positive_samples_file) == 0
    assert _line_count(router.negative_samples_file) == 0
    assert service.get_metrics()["visualize_only_observed"] == 1


def test_queue_worker_processes_tasks_in_background(tmp_path, monkeypatch):
    service, engine, router = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": True,
            "queue_enabled": True,
            "queue_maxsize": 10,
            "drop_when_queue_full": True,
        },
        sleep_seconds=0.01,
    )

    async def _run():
        await service.auto_evaluate_async("query-1", "def a(): pass", "A" * 160, "sid-1")
        await service.auto_evaluate_async("query-2", "def b(): pass", "B" * 160, "sid-2")
        await service.auto_evaluate_async("query-3", "def c(): pass", "C" * 160, "sid-3")
        await asyncio.sleep(0.2)
        await service.shutdown()

    asyncio.run(_run())

    metrics = service.get_metrics()
    assert metrics["enqueued"] == 3
    assert engine.called == 3
    assert metrics["processed"] >= 3
    assert metrics["inflight"] == 0
    assert metrics["terminal_count"] >= metrics["enqueued"]
    assert metrics["is_idle"] is True
    assert _line_count(router.eval_results_file) == 0  # visualize_only


def test_queue_full_drop_is_recorded(tmp_path, monkeypatch):
    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": True,
            "queue_enabled": True,
            "queue_maxsize": 1,
            "drop_when_queue_full": True,
        },
    )

    async def _no_worker_start():
        return None

    service._ensure_worker_started = _no_worker_start  # deterministic: prevent consumer from draining queue

    async def _run():
        await service.auto_evaluate_async("query-a", "def a(): pass", "A" * 160, "s-a")
        await service.auto_evaluate_async("query-b", "def b(): pass", "B" * 160, "s-b")

    asyncio.run(_run())

    metrics = service.get_metrics()
    assert metrics["enqueued"] == 1
    assert metrics["dropped_queue_full"] == 1


def test_duplicate_cache_eviction_is_deterministic(tmp_path, monkeypatch):
    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={"visualize_only": True, "async_evaluation": False},
    )

    for i in range(1001):
        is_dup = service._check_duplicate(query=f"query-{i}", session_id="sid-evict")
        assert is_dup is False

    # 超过阈值后应裁剪为 500，且最早 key 已淘汰。
    assert len(service._evaluated_keys) == 500
    first_key = "sid-evict:" + hashlib.md5("query-0".encode()).hexdigest()[:8]
    assert first_key not in service._evaluated_keys


def test_queue_worker_preserves_trace_context(tmp_path, monkeypatch):
    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": True,
            "queue_enabled": True,
            "queue_maxsize": 10,
            "drop_when_queue_full": True,
        },
    )

    import app.services.auto_evaluation_service as auto_eval_module

    captured = []

    async def _fake_auto_evaluate(**kwargs):
        captured.append(
            (
                auto_eval_module.tracing_service.get_current_trace_id(),
                auto_eval_module.tracing_service.get_current_session_id(),
                kwargs["session_id"],
            )
        )
        return "gold"

    service.auto_evaluate = _fake_auto_evaluate

    async def _run():
        with auto_eval_module.tracing_service.trace_scope("trace-ctx-1", session_id="sid-ctx-1"):
            await service.auto_evaluate_async(
                query="query-trace",
                retrieved_context="def trace(): pass",
                generated_answer="T" * 180,
                session_id="sid-ctx-1",
            )
        await asyncio.sleep(0.1)
        await service.shutdown()

    asyncio.run(_run())

    assert captured == [("trace-ctx-1", "sid-ctx-1", "sid-ctx-1")]


def test_auto_eval_reports_scores_to_langfuse(tmp_path, monkeypatch):
    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={"visualize_only": True, "async_evaluation": False},
    )

    import app.services.auto_evaluation_service as auto_eval_module

    calls = []

    def _record_score(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(auto_eval_module.tracing_service, "record_score", _record_score)

    asyncio.run(
        service.auto_evaluate(
            query="how auth works",
            retrieved_context="def login(): pass",
            generated_answer="A" * 180,
            session_id="sid-score",
            repo_url="https://github.com/a/b",
        )
    )

    score_names = [kwargs["score_name"] for _, kwargs in calls]
    assert "auto_eval.final_score" in score_names
    assert "auto_eval.custom_score" in score_names
    assert "auto_eval.quality_tier" in score_names

    quality_tier_calls = [kwargs for _, kwargs in calls if kwargs["score_name"] == "auto_eval.quality_tier"]
    assert len(quality_tier_calls) == 1
    assert quality_tier_calls[0]["data_type"] == "CATEGORICAL"


def test_ragas_sampling_zero_skips_ragas_call(tmp_path, monkeypatch):
    async def _ragas_eval(self, query, context, answer):
        self._ragas_called = getattr(self, "_ragas_called", 0) + 1
        return 0.9, "ok"

    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": False,
            "use_ragas": True,
            "ragas_sample_rate": 0.0,
        },
        ragas_impl=_ragas_eval,
    )

    asyncio.run(
        service.auto_evaluate(
            query="query-sample-zero",
            retrieved_context="def f(): pass",
            generated_answer="Z" * 200,
            session_id="sid-zero",
        )
    )

    metrics = service.get_metrics()
    assert getattr(service, "_ragas_called", 0) == 0
    assert metrics["ragas_skipped_sampling"] >= 1


def test_ragas_timeout_triggers_circuit_breaker(tmp_path, monkeypatch):
    async def _ragas_eval(self, query, context, answer):
        self._ragas_called = getattr(self, "_ragas_called", 0) + 1
        await asyncio.sleep(0.05)
        return 0.9, "late"

    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": False,
            "use_ragas": True,
            "ragas_sample_rate": 1.0,
            "ragas_timeout_sec": 0.01,
            "ragas_circuit_breaker_enabled": True,
            "ragas_cb_fail_threshold": 2,
            "ragas_cb_reset_sec": 60,
        },
        ragas_impl=_ragas_eval,
    )

    asyncio.run(
        service.auto_evaluate(
            query="query-timeout-1",
            retrieved_context="def t1(): pass",
            generated_answer="T" * 200,
            session_id="sid-t1",
        )
    )
    asyncio.run(
        service.auto_evaluate(
            query="query-timeout-2",
            retrieved_context="def t2(): pass",
            generated_answer="T" * 200,
            session_id="sid-t2",
        )
    )
    asyncio.run(
        service.auto_evaluate(
            query="query-timeout-3",
            retrieved_context="def t3(): pass",
            generated_answer="T" * 200,
            session_id="sid-t3",
        )
    )

    metrics = service.get_metrics()
    assert getattr(service, "_ragas_called", 0) == 2
    assert metrics["ragas_timeouts"] >= 2
    assert metrics["ragas_circuit_open_hits"] >= 1
    assert metrics["ragas_circuit_open"] is True


def test_ragas_eval_uses_dataset_api_phase4(tmp_path, monkeypatch):
    service, _, _ = _build_service(
        tmp_path,
        monkeypatch,
        config_kwargs={
            "visualize_only": True,
            "async_evaluation": False,
            "use_ragas": True,
            "ragas_sample_rate": 1.0,
        },
    )

    captured = {}

    class _FakeDataset:
        @staticmethod
        def from_dict(payload):
            captured["dataset_payload"] = payload
            return {"dataset_object": payload}

    class _FakeResult:
        def __init__(self):
            self.scores = [{"faithfulness": 0.8, "answer_relevancy": 0.6}]

        def __getitem__(self, key):
            raise KeyError(key)

    def _fake_evaluate(*, dataset, metrics, **kwargs):
        captured["dataset"] = dataset
        captured["metrics"] = metrics
        captured["kwargs"] = kwargs
        return _FakeResult()

    ragas_mod = types.ModuleType("ragas")
    ragas_mod.evaluate = _fake_evaluate

    metrics_collections_mod = types.ModuleType("ragas.metrics.collections")
    metrics_collections_mod.faithfulness = types.SimpleNamespace(metric="faith_metric_obj")
    metrics_collections_mod.answer_relevancy = types.SimpleNamespace(metric="answer_metric_obj")

    ragas_metrics_mod = types.ModuleType("ragas.metrics")
    ragas_metrics_mod.collections = metrics_collections_mod

    datasets_mod = types.ModuleType("datasets")
    datasets_mod.Dataset = _FakeDataset

    monkeypatch.setitem(sys.modules, "ragas", ragas_mod)
    monkeypatch.setitem(sys.modules, "ragas.metrics", ragas_metrics_mod)
    monkeypatch.setitem(sys.modules, "ragas.metrics.collections", metrics_collections_mod)
    monkeypatch.setitem(sys.modules, "datasets", datasets_mod)

    score, details = asyncio.run(
        service._ragas_eval(
            query="how auth works",
            context="def login(): pass",
            answer="authentication flow",
        )
    )

    assert captured["dataset_payload"]["question"] == ["how auth works"]
    assert captured["dataset_payload"]["contexts"] == [["def login(): pass"]]
    assert captured["dataset_payload"]["answer"] == ["authentication flow"]
    assert captured["dataset"] == {"dataset_object": captured["dataset_payload"]}
    assert captured["metrics"] == ["faith_metric_obj", "answer_metric_obj"]
    assert captured["kwargs"]["show_progress"] is False
    assert captured["kwargs"]["raise_exceptions"] is False
    assert score == 0.7
    assert "faithfulness=0.800" in details
