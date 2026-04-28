# -*- coding: utf-8 -*-
"""
本地仓库镜像存储层

目标:
1. 提供 repo@commit 的本地只读镜像访问
2. 优先复用本地 mirror，减少 GitHub API 读取压力
3. 镜像不可用时由上层回退到 GitHub API
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from filelock import FileLock, Timeout as FileLockTimeout

from app.utils.github_client import FileFilter, GitHubFile, GitHubRepo
from app.utils.locking import KeyedAsyncLocks

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_repo_segment(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("_", "-", ".") else "_" for c in value)
    return cleaned or "unknown"


@dataclass(frozen=True)
class RepoMirrorSnapshot:
    """本地镜像快照信息 (repo@commit)。"""

    repo_full_name: str
    commit_sha: str
    default_branch: str
    mirror_git_dir: str
    synced_at: float


class RepoMirrorUnavailable(RuntimeError):
    """本地镜像不可用，需要回退到 GitHub API。"""


class RepoMirrorStore:
    """
    本地仓库镜像管理器。

    存储结构:
    - {base_dir}/repos/{owner}__{repo}.git  (bare mirror)
    """

    def __init__(
        self,
        *,
        base_dir: Optional[str] = None,
        enabled: Optional[bool] = None,
        sync_ttl_seconds: float = 30.0,
        clone_timeout_seconds: float = 120.0,
        fetch_timeout_seconds: float = 60.0,
        command_timeout_seconds: float = 20.0,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        self.enabled = _env_bool("GITHUB_MIRROR_ENABLED", True) if enabled is None else enabled
        base_path = base_dir or os.getenv("GITHUB_MIRROR_DIR", "data/repo_mirrors")
        self.base_dir = Path(base_path).resolve()
        self.mirrors_dir = self.base_dir / "repos"
        self.mirrors_dir.mkdir(parents=True, exist_ok=True)

        self.sync_ttl_seconds = sync_ttl_seconds
        self.clone_timeout_seconds = clone_timeout_seconds
        self.fetch_timeout_seconds = fetch_timeout_seconds
        self.command_timeout_seconds = command_timeout_seconds
        self.lock_timeout_seconds = lock_timeout_seconds

        self._locks = KeyedAsyncLocks()
        self._snapshot_cache: Dict[str, RepoMirrorSnapshot] = {}

    async def get_repo_tree(
        self,
        repo: GitHubRepo,
        file_filter: Optional[FileFilter] = None,
    ) -> List[GitHubFile]:
        """读取 repo@commit 文件树（只读）。"""
        snapshot = await self._get_snapshot(repo)
        filter_config = file_filter or FileFilter()
        return await asyncio.to_thread(self._list_files_at_commit, snapshot, filter_config)

    async def get_file_content(
        self,
        repo: GitHubRepo,
        path: str,
    ) -> Optional[str]:
        """读取 repo@commit 指定文件内容（UTF-8）。"""
        snapshot = await self._get_snapshot(repo)
        return await asyncio.to_thread(self._read_file_at_commit, snapshot, path)

    async def _get_snapshot(self, repo: GitHubRepo) -> RepoMirrorSnapshot:
        if not self.enabled:
            raise RepoMirrorUnavailable("仓库镜像功能已禁用")

        cache_key = repo.full_name.lower()
        snapshot = self._snapshot_cache.get(cache_key)
        now = time.time()
        if snapshot and now - snapshot.synced_at < self.sync_ttl_seconds:
            return snapshot

        acquired = await self._locks.acquire(cache_key, timeout=self.lock_timeout_seconds)
        if not acquired:
            raise RepoMirrorUnavailable(f"仓库镜像锁超时: {repo.full_name}")

        try:
            snapshot = self._snapshot_cache.get(cache_key)
            now = time.time()
            if snapshot and now - snapshot.synced_at < self.sync_ttl_seconds:
                return snapshot

            snapshot = await asyncio.to_thread(self._sync_and_resolve_snapshot, repo)
            self._snapshot_cache[cache_key] = snapshot
            return snapshot
        finally:
            await self._locks.release(cache_key)

    def _sync_and_resolve_snapshot(self, repo: GitHubRepo) -> RepoMirrorSnapshot:
        git_dir = self._mirror_git_dir(repo)
        remote_url = f"https://github.com/{repo.owner}/{repo.name}.git"
        lock_path = f"{git_dir}.sync.lock"
        lock = FileLock(lock_path, timeout=self.lock_timeout_seconds)

        try:
            with lock:
                if not git_dir.exists():
                    self._run_git(
                        ["clone", "--mirror", remote_url, str(git_dir)],
                        timeout=self.clone_timeout_seconds,
                    )
                else:
                    self._run_git(
                        ["--git-dir", str(git_dir), "remote", "set-url", "origin", remote_url],
                        timeout=self.command_timeout_seconds,
                        check=False,
                    )
                    self._run_git(
                        ["--git-dir", str(git_dir), "fetch", "--prune", "origin"],
                        timeout=self.fetch_timeout_seconds,
                    )

                commit_sha = self._resolve_branch_head(git_dir, repo.default_branch)
                return RepoMirrorSnapshot(
                    repo_full_name=repo.full_name,
                    commit_sha=commit_sha,
                    default_branch=repo.default_branch,
                    mirror_git_dir=str(git_dir),
                    synced_at=time.time(),
                )
        except FileLockTimeout as e:
            raise RepoMirrorUnavailable(f"仓库镜像同步锁超时: {repo.full_name}") from e

    def _resolve_branch_head(self, git_dir: Path, default_branch: str) -> str:
        candidate_refs = [
            f"refs/remotes/origin/{default_branch}",
            f"origin/{default_branch}",
            default_branch,
            "HEAD",
        ]
        for ref in candidate_refs:
            result = self._run_git(
                ["--git-dir", str(git_dir), "rev-parse", "--verify", ref],
                timeout=self.command_timeout_seconds,
                check=False,
            )
            sha = (result.stdout or "").strip()
            if result.returncode == 0 and sha:
                return sha
        raise RepoMirrorUnavailable(f"无法解析仓库提交: {git_dir} ({default_branch})")

    def _list_files_at_commit(
        self,
        snapshot: RepoMirrorSnapshot,
        file_filter: FileFilter,
    ) -> List[GitHubFile]:
        result = self._run_git(
            ["--git-dir", snapshot.mirror_git_dir, "ls-tree", "-r", "-l", snapshot.commit_sha],
            timeout=self.command_timeout_seconds,
        )
        files: List[GitHubFile] = []
        for raw_line in result.stdout.splitlines():
            if "\t" not in raw_line:
                continue
            meta, path = raw_line.split("\t", 1)
            parts = meta.split()
            if len(parts) < 4:
                continue
            file_type = parts[1]
            sha = parts[2]
            size_str = parts[3]
            try:
                size = int(size_str)
            except ValueError:
                size = 0
            file = GitHubFile(path=path, type=file_type, size=size, sha=sha)
            if file_filter.should_include(file):
                files.append(file)
        return files

    def _read_file_at_commit(self, snapshot: RepoMirrorSnapshot, path: str) -> Optional[str]:
        object_ref = f"{snapshot.commit_sha}:{path}"
        object_type = self._run_git(
            ["--git-dir", snapshot.mirror_git_dir, "cat-file", "-t", object_ref],
            timeout=self.command_timeout_seconds,
            check=False,
        )
        if object_type.returncode != 0:
            return None
        if (object_type.stdout or "").strip() != "blob":
            return None

        data = self._run_git_bytes(
            ["--git-dir", snapshot.mirror_git_dir, "show", object_ref],
            timeout=self.command_timeout_seconds,
            check=False,
        )
        if data.returncode != 0:
            return None
        try:
            return data.stdout.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("镜像文件无法解码为 UTF-8: %s (%s)", path, snapshot.repo_full_name)
            return None

    def _mirror_git_dir(self, repo: GitHubRepo) -> Path:
        owner = _sanitize_repo_segment(repo.owner)
        name = _sanitize_repo_segment(repo.name)
        return self.mirrors_dir / f"{owner}__{name}.git"

    def _run_git(
        self,
        args: List[str],
        *,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise RepoMirrorUnavailable("系统未安装 git，无法使用本地镜像") from e
        except subprocess.TimeoutExpired as e:
            raise RepoMirrorUnavailable(f"git 命令超时 ({timeout}s): {' '.join(args[:4])}") from e

        if check and result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RepoMirrorUnavailable(
                f"git 命令失败({result.returncode}): {stderr[:240]}"
            )
        return result

    def _run_git_bytes(
        self,
        args: List[str],
        *,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise RepoMirrorUnavailable("系统未安装 git，无法使用本地镜像") from e
        except subprocess.TimeoutExpired as e:
            raise RepoMirrorUnavailable(f"git 命令超时 ({timeout}s): {' '.join(args[:4])}") from e

        if check and result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RepoMirrorUnavailable(
                f"git 命令失败({result.returncode}): {stderr[:240]}"
            )
        return result


__all__ = [
    "RepoMirrorStore",
    "RepoMirrorSnapshot",
    "RepoMirrorUnavailable",
]
