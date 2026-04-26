# -*- coding: utf-8 -*-
from app.services.tracing_service import TracingConfig, TracingService
from app.storage.runtime_store import RuntimeTraceStore


def test_runtime_trace_store_lifecycle(tmp_path):
    store = RuntimeTraceStore(db_path=str(tmp_path / "runtime_traces.db"))

    trace_id = "trace-lifecycle-1"
    store.start_run(
        run_id=trace_id,
        trace_id=trace_id,
        session_id="session-1",
        trace_name="agent_analysis",
        metadata={"repo_url": "https://github.com/acme/demo"},
    )
    store.add_step(
        trace_id=trace_id,
        step_name="init",
        status="info",
        message="starting",
        payload={"round": 0},
    )
    store.add_tool_call(
        trace_id=trace_id,
        tool_name="github.get_file_content",
        parameters={"file_path": "README.md"},
        result_preview="ok",
        latency_ms=12.5,
        success=True,
    )
    store.finish_run(
        trace_id=trace_id,
        status="completed",
        metadata={"stream_completed": True},
    )

    run = store.get_run_by_trace_id(trace_id)
    assert run is not None
    assert run["status"] == "completed"
    assert run["ended_at"] is not None

    steps = store.list_steps(trace_id)
    assert len(steps) == 1
    assert steps[0]["step_name"] == "init"

    tool_calls = store.list_tool_calls(trace_id)
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "github.get_file_content"
    assert tool_calls[0]["success"] == 1


def test_tracing_service_persists_runtime_records(tmp_path):
    config = TracingConfig(
        enabled=False,
        backend="local",
        local_log_dir=str(tmp_path / "trace_logs"),
    )
    service = TracingService(config=config)
    store = RuntimeTraceStore(db_path=str(tmp_path / "service_runtime.db"))
    service.runtime_store = store

    trace_id = service.start_trace(
        trace_name="chat_session",
        session_id="session-42",
        metadata={"repo_url": "https://github.com/acme/repo"},
    )
    service.add_event("retrieval_completed", {"message": "retrieved"})
    service.record_step("custom_step", status="info", message="manual")
    service.record_tool_call(
        tool_name="vector.add_documents",
        parameters={"documents": 3},
        result={"indexed_documents": 3},
        latency_ms=5.2,
        success=True,
    )
    service.end_trace({"stream_completed": True})

    run = store.get_run_by_trace_id(trace_id)
    assert run is not None
    assert run["session_id"] == "session-42"
    assert run["status"] == "completed"

    step_names = [row["step_name"] for row in store.list_steps(trace_id)]
    assert "retrieval_completed" in step_names
    assert "custom_step" in step_names
    assert "trace_end" in step_names

    tool_calls = store.list_tool_calls(trace_id)
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "vector.add_documents"
