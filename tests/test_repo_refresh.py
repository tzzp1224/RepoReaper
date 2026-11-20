import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app.services import repo_refresh_service
from app.services.repo_refresh_service import RepoRefreshService, parse_delivery
from app.storage.repo_refresh_store import RepoRefreshStore


def test_push_signature_and_default_branch_scope():
    def push(branch="main", name="owner/repo"):
        raw = json.dumps({"ref": f"refs/heads/{branch}", "after": "a"*40,
                          "repository": {"full_name": name, "default_branch": "main"}}).encode()
        return raw, "sha256=" + hmac.new(b"fixture", raw, hashlib.sha256).hexdigest()

    raw, signature = push()
    assert parse_delivery(raw, signature, "push", "fixture", {"owner/repo"}).revision == "a"*40
    with pytest.raises(PermissionError):
        parse_delivery(raw, signature, "push", "different secret", {"owner/repo"})
    assert parse_delivery(*push(branch="feature"), "push", "fixture", {"owner/repo"}) is None
    assert parse_delivery(*push(name="other/repo"), "push", "fixture", {"owner/repo"}) is None


def test_refresh_deduplicates_and_coalesces_without_losing_new_push(tmp_path):
    path = tmp_path / "refresh.sqlite"
    first = RepoRefreshStore(path)
    second = RepoRefreshStore(path)
    assert first.enqueue("delivery-a", "owner/repo", "a"*40)
    assert not second.enqueue("delivery-a", "owner/repo", "a"*40)
    claim = first.claim()
    assert claim["desired"] == "a"*40 and second.claim() is None
    assert second.enqueue("delivery-b", "owner/repo", "b"*40)
    assert second.claim() is None
    first.finish(claim, published="a"*40)
    next_job = second.claim()
    assert next_job["desired"] == "b"*40 and next_job["attempts"] == 1
    first.finish(claim, published="a"*40)  # Stale completion must not overwrite the newer lease.
    second.finish(next_job, published="c"*40)
    assert first.status("owner/repo") == {"desired": "b"*40, "status": "completed",
                                         "published": "c"*40, "error": None}


def test_refresh_publishes_actual_head_after_retry(monkeypatch, tmp_path):
    sent_commit, head_commit = "a" * 40, "b" * 40
    now = [1000.0]
    calls = []
    monkeypatch.setenv("REPO_WEBHOOK_SECRET", "fixture")
    monkeypatch.setenv("REPO_WEBHOOK_REPOSITORIES", "owner/repo")
    monkeypatch.setattr("app.storage.repo_refresh_store.time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(repo_refresh_service.store_manager, "get_store", lambda _: SimpleNamespace(
        load_context=lambda: {"snapshot_index": {"revision": "c" * 40}, "report_language": "zh"},
    ))

    async def analyze(url, session, *, language):
        calls.append((url, language))
        if len(calls) == 1:
            yield json.dumps({"step": "error"})
        else:
            yield json.dumps({"step": "finish", "commit": head_commit})

    monkeypatch.setattr(repo_refresh_service, "agent_stream", analyze)
    path = tmp_path / "refresh.sqlite"
    store = RepoRefreshStore(path)
    raw = json.dumps({"ref": "refs/heads/main", "after": sent_commit,
                      "repository": {"full_name": "owner/repo", "default_branch": "main"}}).encode()
    signature = "sha256=" + hmac.new(b"fixture", raw, hashlib.sha256).hexdigest()

    async def run():
        service = RepoRefreshService(store)
        assert await service.accept(raw=raw, signature=signature, event="push", delivery_id="delivery-a")
        await service._refresh(store.claim())
        assert store.claim() is None  # Retry is scheduled after a backoff.
        now[0] += 15
        reopened = RepoRefreshStore(path)
        await RepoRefreshService(reopened)._refresh(reopened.claim())
        assert reopened.status("owner/repo") == {"desired": sent_commit, "status": "completed",
                                                  "published": head_commit, "error": None}

    asyncio.run(run())
    assert calls == [("https://github.com/owner/repo", "zh")] * 2
