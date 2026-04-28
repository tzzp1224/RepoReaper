import asyncio
import json
from contextlib import asynccontextmanager

import app.services.agent_service as agent_service
from app.utils.session import generate_repo_lock_key


def test_generate_repo_lock_key_normalizes_equivalent_urls():
    key_https = generate_repo_lock_key("https://github.com/Owner/Repo.git")
    key_short = generate_repo_lock_key("github.com/owner/repo")
    key_ssh = generate_repo_lock_key("git@github.com:owner/repo.git")

    assert key_https == key_short == key_ssh


def test_resolve_repo_lock_key_falls_back_to_session():
    lock_key = agent_service._resolve_repo_lock_key("", "session-123")
    assert lock_key == "session-123"


def test_agent_stream_uses_repo_level_lock_key(monkeypatch):
    repo_url = "https://github.com/acme/demo"
    session_id = "session-A"
    expected_lock_key = generate_repo_lock_key(repo_url)
    calls = {}

    async def fake_is_locked(cls, lock_key: str):
        calls["is_locked"] = lock_key
        return False

    @asynccontextmanager
    async def _fake_lock_cm(lock_key: str):
        calls["acquire"] = lock_key
        yield

    def fake_acquire(cls, lock_key: str, timeout: float = None):
        return _fake_lock_cm(lock_key)

    async def fake_inner(
        repo_url_arg,
        session_id_arg,
        language_arg,
        regenerate_only_arg,
        short_id_arg,
        trace_id_arg,
        start_time_arg,
    ):
        assert repo_url_arg == repo_url
        assert session_id_arg == session_id
        yield json.dumps({"step": "finish", "message": "done"})

    monkeypatch.setattr(agent_service.RepoLock, "is_locked", classmethod(fake_is_locked))
    monkeypatch.setattr(agent_service.RepoLock, "acquire", classmethod(fake_acquire))
    monkeypatch.setattr(agent_service, "_agent_stream_inner", fake_inner)
    monkeypatch.setattr(agent_service.tracing_service, "start_trace", lambda *args, **kwargs: "trace-1")
    monkeypatch.setattr(agent_service.tracing_service, "record_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_service.tracing_service, "end_trace", lambda *args, **kwargs: None)

    events = []

    async def _collect():
        async for raw in agent_service.agent_stream(repo_url, session_id):
            events.append(json.loads(raw))

    asyncio.run(_collect())

    assert calls["is_locked"] == expected_lock_key
    assert calls["acquire"] == expected_lock_key
    assert events
    assert events[-1]["step"] == "finish"
