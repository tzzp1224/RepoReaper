"""Read-only repository snapshots. Context-local binding also covers legacy callers."""
import asyncio
import re
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.utils.github_client import FileFilter, GitHubFile, parse_repo_url


@dataclass(frozen=True)
class BlobEntry:
    sha: str
    size: int


def decode_text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def discovery_paths(entries: dict[str, BlobEntry]) -> list[str]:
    file_filter = FileFilter()
    return sorted(path for path, entry in entries.items()
                  if file_filter.should_include(GitHubFile(path, "blob", entry.size)) and safe_source_path(path))


def safe_source_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or ".." in parts or any(ord(char) < 32 for char in path):
        return False
    filename = parts[-1].lower()
    return not (filename == ".env" or filename.startswith(".env.")
                and filename not in {".env.example", ".env.sample", ".env.template"})


class RepositorySource(Protocol):
    repo: str
    revision: str

    async def list_files(self) -> list[str]: ...
    async def read_file(self, path: str) -> str | None: ...
    async def manifest(self) -> dict[str, BlobEntry]: ...


_source: ContextVar[RepositorySource | None] = ContextVar("repository_source", default=None)


@contextmanager
def use_repository_source(source: RepositorySource):
    token = _source.set(source)
    try:
        yield source
    finally:
        _source.reset(token)


def bound_source(repo_url: str) -> RepositorySource | None:
    source = _source.get()
    if source is not None:
        repo = parse_repo_url(repo_url)
        if not repo or "/".join(repo).lower() != source.repo.lower():
            raise ValueError("Snapshot repository mismatch; live fallback is forbidden")
    return source


class GitSnapshotSource:
    """Read Git objects at a full commit SHA, never the mutable working tree.

    Symlinks/submodules are excluded. The same production file filter is used
    for discovery; direct reads may access other regular text files via JIT.
    """
    def __init__(self, root: str | Path, repo: str, revision: str):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError("A full 40-character commit SHA is required")
        self.root = Path(root).resolve()
        self.repo = repo
        self.revision = revision.lower()
        resolved = self._git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
        if resolved != self.revision:
            raise ValueError("Commit mismatch")
        self.reads: list[dict] = []
        self._entries: dict[str, BlobEntry] | None = None
        self._manifest_lock = asyncio.Lock()

    def _git(self, *args: str, timeout: float = 60) -> bytes:
        return subprocess.run(["git", "--no-replace-objects", "-C", str(self.root), *args], check=True,
                              capture_output=True, timeout=timeout).stdout

    def _load_entries(self) -> dict[str, BlobEntry]:
        entries = {}
        for record in self._git("ls-tree", "-rlz", self.revision).split(b"\0"):
            if not record:
                continue
            header, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            if kind == b"blob" and mode in (b"100644", b"100755"):
                try:
                    path = raw_path.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                entries[path] = BlobEntry(oid.decode(), int(size))
        return entries

    async def manifest(self) -> dict[str, BlobEntry]:
        async with self._manifest_lock:
            if self._entries is None:
                self._entries = await asyncio.to_thread(self._load_entries)
            return self._entries

    async def list_files(self) -> list[str]:
        return discovery_paths(await self.manifest())

    async def read_file(self, path: str) -> str | None:
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or "\x00" in path:
            raise ValueError("Invalid snapshot path")
        entry = (await self.manifest()).get(path)
        result = None
        if entry and entry.size <= FileFilter.max_file_size:
            data = await asyncio.to_thread(self._git, "cat-file", "blob", entry.sha)
            result = decode_text(data)
        self.reads.append({"path": path, "revision": self.revision,
                           "success": result is not None, "chars": len(result or "")})
        return result

    async def find_literal_paths(self, symbols: list[str]) -> list[str]:
        if not symbols:
            return []
        args = ["grep", "-IlzFw"]
        for symbol in symbols:
            args.extend(("-e", symbol))
        try:
            result = await asyncio.to_thread(self._git, *args, self.revision, "--", timeout=1.0)
        except subprocess.SubprocessError:
            return []
        prefix = self.revision.encode() + b":"
        return [path.removeprefix(prefix).decode("utf-8", errors="replace") for path in result.split(b"\0") if path]
