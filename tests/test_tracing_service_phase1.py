from app.services.tracing_service import TracingConfig, TracingService


def _build_service(tmp_path):
    service = TracingService(
        config=TracingConfig(
            enabled=False,
            backend="local",
            local_log_dir=str(tmp_path / "traces"),
        )
    )
    service.clear_trace_context()
    return service


def test_trace_scope_restores_previous_context(tmp_path):
    service = _build_service(tmp_path)

    assert service.get_current_trace_id() is None
    assert service.get_current_session_id() is None

    with service.trace_scope("trace-1", session_id="session-1"):
        assert service.get_current_trace_id() == "trace-1"
        assert service.get_current_session_id() == "session-1"

        with service.trace_scope("trace-2", session_id="session-2"):
            assert service.get_current_trace_id() == "trace-2"
            assert service.get_current_session_id() == "session-2"

        assert service.get_current_trace_id() == "trace-1"
        assert service.get_current_session_id() == "session-1"

    assert service.get_current_trace_id() is None
    assert service.get_current_session_id() is None


def test_add_event_includes_trace_context_for_create_event(tmp_path):
    service = _build_service(tmp_path)

    class FakeLangfuseClient:
        def __init__(self):
            self.calls = []

        def create_event(self, *, trace_context=None, name, input=None, output=None, metadata=None):
            self.calls.append(
                {
                    "trace_context": trace_context,
                    "name": name,
                    "input": input,
                    "output": output,
                    "metadata": metadata,
                }
            )
            return {"ok": True}

    client = FakeLangfuseClient()
    service.langfuse_client = client

    with service.trace_scope("trace-x", session_id="session-x"):
        service.add_event("unit_event", {"k": "v"})

    assert len(client.calls) == 1
    payload = client.calls[0]
    assert payload["trace_context"] == {"trace_id": "trace-x"}
    assert payload["metadata"]["trace_id"] == "trace-x"
    assert payload["metadata"]["session_id"] == "session-x"


def test_start_trace_and_end_trace_manage_context(tmp_path):
    service = _build_service(tmp_path)

    trace_id = service.start_trace("unit_trace", "session-123", {"repo_url": "https://example.com/repo"})
    assert trace_id
    assert service.get_current_trace_id() == trace_id
    assert service.get_current_session_id() == "session-123"

    service.end_trace({"done": True})
    assert service.get_current_trace_id() is None
    assert service.get_current_session_id() is None


def test_record_score_uses_create_score_with_trace_context(tmp_path):
    service = _build_service(tmp_path)

    class FakeLangfuseClient:
        def __init__(self):
            self.calls = []

        def create_score(
            self,
            *,
            name,
            value,
            session_id=None,
            trace_id=None,
            observation_id=None,
            data_type=None,
            comment=None,
            metadata=None,
        ):
            self.calls.append(
                {
                    "name": name,
                    "value": value,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "observation_id": observation_id,
                    "data_type": data_type,
                    "comment": comment,
                    "metadata": metadata,
                }
            )

    client = FakeLangfuseClient()
    service.langfuse_client = client

    with service.trace_scope("trace-score", session_id="session-score"):
        service.record_score(
            score_name="auto_eval.final_score",
            value=0.88,
            metadata={"source": "unit_test"},
        )

    assert len(client.calls) == 1
    payload = client.calls[0]
    assert payload["name"] == "auto_eval.final_score"
    assert payload["trace_id"] == "trace-score"
    assert payload["session_id"] == "session-score"
    assert payload["data_type"] == "NUMERIC"
    assert payload["metadata"]["source"] == "unit_test"
