"""Bounded, read-only discovery and source evidence for an immutable repository."""

import ast
import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.services.repository_source import RepositorySource, safe_source_path
from app.utils.github_client import FileFilter, GitHubFile


def source_chunks(path: str, content: str) -> list[tuple[str, dict]]:
    """Split at Python declaration boundaries and keep contiguous original lines."""
    lines = content.splitlines(keepends=True)
    starts = {0: ""}
    if path.endswith(".py"):
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError, RecursionError):
            tree = None
        if tree:
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([node.lineno, *(deco.lineno for deco in node.decorator_list)]) - 1
                    starts[start] = node.name

    chunks = []
    boundaries = sorted(starts)
    for start, stop in zip(boundaries, boundaries[1:] + [len(lines)]):
        for offset in range(start, stop, 28):
            end = min(offset + 28, stop)
            text = "".join(lines[offset:end]).rstrip("\r\n")
            if text.strip():
                chunks.append((text, {"file": path, "start_line": offset + 1, "end_line": end,
                                      "name": starts[start], "type": "source"}))
    return chunks


_WORDS = re.compile(r"[a-z][a-z0-9_]*|[\u4e00-\u9fff]+", re.IGNORECASE)
_STOP = set("the and for with from into what where how does this that which file code implementation repo".split())
_DISCOVERY_FILTER = FileFilter()
_DISCOVERY_FILTER.ignored_directories -= {".github", "migrations"}


def query_terms(text: str) -> set[str]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {term for term in _WORDS.findall(text.lower()) if len(term) >= 2 and term not in _STOP}


@dataclass(frozen=True)
class EvidenceSkill:
    instruction: str
    path_prefix: str = ""
    follow_references: bool = False


def select_skill(query: str) -> EvidenceSkill:
    if re.search(r"\b(ci|lint|workflow|github actions|job matrix)\b|持续集成|工作流", query, re.I):
        return EvidenceSkill("Trace workflow triggers, job dependencies and invoked scripts when relevant.",
                             ".github/workflows/", True)
    follow = bool(re.search(r"\b(calls?|invokes?|flow|pipeline|depends?)\b|调用|流程|依赖|链路", query, re.I))
    return EvidenceSkill("Trace definitions, call sites and configuration relevant to the question.",
                         follow_references=follow)


@dataclass(frozen=True)
class ReadResult:
    path: str
    status: str
    spans: tuple[dict, ...] = ()
    latency_ms: float = 0.0


class RepositoryEvidence:
    def __init__(self, source: RepositorySource, *, max_parallel: int = 4):
        self.source = source
        self._limit = asyncio.Semaphore(max_parallel)

    async def find_paths(self, query: str, skill: EvidenceSkill, indexed: list[dict], limit: int = 8) -> list[str]:
        manifest = await self.source.manifest()
        terms = query_terms(query)
        query_folded = query.lower()
        known_paths = {item["file"] for item in indexed}
        lookup = getattr(self.source, "find_literal_paths", None)
        identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", query)
        names = [name for name in identifiers if "_" in name or re.search(r"[a-z][A-Z]", name)
                 or f"`{name}`" in query]
        symbol_paths = set(await lookup(list(dict.fromkeys(names))[:5])) if lookup and names else set()
        matches = []
        for path, entry in manifest.items():
            if not safe_source_path(path) or not _DISCOVERY_FILTER.should_include(GitHubFile(path, "blob", entry.size)):
                continue
            base = PurePosixPath(path).name
            score = 3 * len(terms & query_terms(base)) + len(terms & query_terms(path))
            folded_path = path.lower()
            literal = folded_path in query_folded and re.search(
                r"(?<![\w./-])" + re.escape(folded_path) + r"(?![\w./-])", query_folded)
            if literal:
                score += 20
            if skill.path_prefix and path.startswith(skill.path_prefix):
                score += 8
            if path in known_paths:
                score += 2
            if path in symbol_paths:
                score += 6
            if score:
                matches.append((score, path))
        return [path for _, path in sorted(matches, key=lambda match: (-match[0], match[1]))[:limit]]

    async def read(self, path: str, query: str, hints: list[dict]) -> ReadResult:
        manifest = await self.source.manifest()
        if not path or path.startswith("/") or ".." in PurePosixPath(path).parts or "\x00" in path:
            return ReadResult(path, "invalid_path")
        if path not in manifest:
            prefix = path.rstrip("/") + "/"
            return ReadResult(path, "directory" if any(p.startswith(prefix) for p in manifest) else "not_found")
        entry = manifest[path]
        if not safe_source_path(path) or not _DISCOVERY_FILTER.should_include(GitHubFile(path, "blob", entry.size)):
            return ReadResult(path, "excluded")
        started = time.perf_counter()
        try:
            async with self._limit:
                content = await asyncio.wait_for(self.source.read_file(path), 3.0)
        except Exception:
            return ReadResult(path, "unavailable", latency_ms=(time.perf_counter() - started) * 1000)
        if content is None:
            return ReadResult(path, "unreadable", latency_ms=(time.perf_counter() - started) * 1000)
        if not content.strip():
            return ReadResult(path, "empty", latency_ms=(time.perf_counter() - started) * 1000)

        terms = query_terms(query)
        hinted_lines = {item["metadata"]["start_line"] for item in hints if item["file"] == path}
        chunks = await asyncio.to_thread(source_chunks, path, content)

        def relevance(chunk):
            text, meta = chunk
            has_hint = any(meta["start_line"] <= line <= meta["end_line"] for line in hinted_lines)
            return 5 * has_hint + 3 * len(terms & query_terms(meta["name"])) + len(terms & query_terms(text))

        chunks.sort(key=relevance, reverse=True)
        spans = tuple({"file": path, "content": text,
                       "metadata": {**meta, "commit": self.source.revision, "blob": entry.sha}}
                      for text, meta in chunks[:3])
        return ReadResult(path, "ok", spans, (time.perf_counter() - started) * 1000)

    async def read_batch(self, paths: list[str], query: str, hints: list[dict]) -> list[ReadResult]:
        return await asyncio.gather(*(self.read(path, query, hints) for path in paths))

    async def related_paths(self, results: list[ReadResult], query: str) -> list[str]:
        references = []
        for result in results:
            for span in result.spans[:1]:
                text = span["content"]
                paths = re.findall(r"[\w./-]+\.(?:py|sh|yml|yaml|js|ts|json|toml)\b", text)
                imports = re.findall(r"\bfrom\s+([\w.]+)\s+import\s+([a-zA-Z_]\w*)", text)
                calls = re.findall(r"\b([A-Za-z_]\w+_[A-Za-z_0-9]+)\s*\(", text)
                references.extend(paths + [f"{module} `{name}`" for module, name in imports] + calls)
        if not references:
            return []
        related_query = query + "\n" + " ".join(list(dict.fromkeys(references))[:8])
        paths = await self.find_paths(related_query, EvidenceSkill(""), [])
        read = {result.path for result in results}
        return [path for path in paths if path not in read][:2]


def build_evidence(results: list[ReadResult], *, max_bytes: int = 10_000) -> tuple[str, list[dict]]:
    """Bound UTF-8 input size and keep each returned citation aligned with its text."""
    evidence = []
    blocks = []
    live = [result.spans for result in results if result.spans]
    for depth in range(max(map(len, live), default=0)):
        for spans in live:
            if depth >= len(spans):
                continue
            item = spans[depth]
            meta = item["metadata"]
            label = f"E{len(evidence) + 1}"
            start = meta["start_line"]
            prefix = f"[{label}] {item['file']}@{meta['commit']}:{start}"
            lines = []
            size = len(prefix.encode()) + 20
            for line in item["content"].splitlines(keepends=True):
                line_size = len(line.encode())
                if size + line_size > min(max_bytes, 2_800):
                    break
                lines.append(line)
                size += line_size
            if not lines:
                continue
            end = start + len(lines) - 1
            content = "".join(lines).rstrip("\r\n")
            blocks.append(f"{prefix}-{end}\n{content}")
            evidence.append({**item, "id": label, "content": content,
                             "metadata": {**meta, "end_line": end}})
            max_bytes -= size
    return "\n\n".join(blocks), evidence
