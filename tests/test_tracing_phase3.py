from app.services import tracing_service as tracing_module


class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []

    def create_score(self, **kwargs):
        self.calls.append(("create_score", kwargs))
        return None

    def score_current_trace(self, **kwargs):
        self.calls.append(("score_current_trace", kwargs))
        return None

    def flush(self):
        self.calls.append(("flush", {}))

    def shutdown(self):
        self.calls.append(("shutdown", {}))


def test_tracing_falls_back_to_local_when_langfuse_keys_missing(monkeypatch, tmp_path):
    class _ShouldNotBeInitialized:
        def __init__(self, **kwargs):
            raise AssertionError("Langfuse client should not be initialized when keys are missing")

    monkeypatch.setattr(tracing_module, "LANGFUSE_AVAILABLE", True)
    monkeypatch.setattr(tracing_module, "Langfuse", _ShouldNotBeInitialized, raising=False)

    service = tracing_module.TracingService(
        config=tracing_module.TracingConfig(
            enabled=True,
            backend="langfuse",
            langfuse_host="http://localhost:3000",
            langfuse_public_key="",
            langfuse_secret_key="",
            local_log_dir=str(tmp_path),
        )
    )

    assert service.config.backend == "local"
    assert service.langfuse_client is None


def test_record_score_prefers_create_score_over_score_current_trace(tmp_path):
    service = tracing_module.TracingService(
        config=tracing_module.TracingConfig(
            enabled=True,
            backend="local",
            local_log_dir=str(tmp_path),
        )
    )
    fake = _FakeLangfuseClient()
    service.langfuse_client = fake
    service._set_trace_context("trace-phase3", "sid-phase3")

    service.record_score("auto_eval.final_score", 0.91, metadata={"phase": 3})

    method_names = [name for name, _ in fake.calls]
    assert "create_score" in method_names
    assert "score_current_trace" not in method_names


def test_tracing_shutdown_flushes_client(tmp_path):
    service = tracing_module.TracingService(
        config=tracing_module.TracingConfig(
            enabled=True,
            backend="local",
            local_log_dir=str(tmp_path),
        )
    )
    fake = _FakeLangfuseClient()
    service.langfuse_client = fake

    service.shutdown()

    method_names = [name for name, _ in fake.calls]
    assert "flush" in method_names
    assert "shutdown" in method_names
