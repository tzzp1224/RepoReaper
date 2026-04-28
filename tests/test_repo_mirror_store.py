import asyncio
import time

import pytest

from app.storage.repo_mirror_store import RepoMirrorSnapshot, RepoMirrorStore, RepoMirrorUnavailable
from app.utils.github_client import GitHubFile, GitHubRepo


def test_repo_mirror_snapshot_cache_reuses_commit(tmp_path, monkeypatch):
    store = RepoMirrorStore(
        base_dir=str(tmp_path / "repo_mirrors"),
        enabled=True,
        sync_ttl_seconds=600.0,
    )
    repo = GitHubRepo(owner="acme", name="demo", default_branch="main")

    calls = {"sync": 0}

    def fake_sync(repo_info):
        calls["sync"] += 1
        return RepoMirrorSnapshot(
            repo_full_name=repo_info.full_name,
            commit_sha="abc123",
            default_branch=repo_info.default_branch,
            mirror_git_dir=str(tmp_path / "repo_mirrors" / "repos" / "acme__demo.git"),
            synced_at=time.time(),
        )

    def fake_list(snapshot, file_filter):
        return [GitHubFile(path="src/main.py", type="blob", size=10, sha="deadbeef")]

    monkeypatch.setattr(store, "_sync_and_resolve_snapshot", fake_sync)
    monkeypatch.setattr(store, "_list_files_at_commit", fake_list)

    first = asyncio.run(store.get_repo_tree(repo))
    second = asyncio.run(store.get_repo_tree(repo))

    assert calls["sync"] == 1
    assert [f.path for f in first] == ["src/main.py"]
    assert [f.path for f in second] == ["src/main.py"]


def test_repo_mirror_disabled_raises(tmp_path):
    store = RepoMirrorStore(
        base_dir=str(tmp_path / "repo_mirrors"),
        enabled=False,
    )
    repo = GitHubRepo(owner="acme", name="demo", default_branch="main")

    with pytest.raises(RepoMirrorUnavailable):
        asyncio.run(store.get_repo_tree(repo))
