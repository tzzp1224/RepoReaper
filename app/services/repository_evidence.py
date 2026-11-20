"""Bounded, read-only discovery and source evidence for an immutable repository."""

import ast
import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.services.repository_source import GrepHit, RepositorySource, safe_source_path
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


def identifier_terms(text: str) -> set[str]:
    terms = query_terms(text)
    return terms | {piece for term in terms for piece in term.split("_") if len(piece) >= 2}


_PATH_STOP = (_STOP | set("""
    about an are as at be been by can could defined did do during for from had has have if in is it its
    like may might multiple of on or rather should some such than their them then these they those
    through to under up use used using was were when who will within would why you your
    specifically implements implement implemented processing system efficient efficiently different vs
""".split())) - {"file"}
_SUPPORT_DIRS = {"doc", "docs", "documentation", "example", "examples", "gallery", "test", "tests",
                 "fixtures", "testsuite", "tutorial", "tutorials"}
_SUPPORT_SUFFIXES = {".md", ".rst", ".adoc", ".ipynb", ".csv", ".parquet"}
SUPPORT_TOPICS = {"test", "fixture", "example", "tutorial", "documentation", "coverage", "pytest"}
_CI_FILES = {".travis.yml", ".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "tox.ini",
             "noxfile.py", "jenkinsfile"}


def source_terms(text: str) -> set[str]:
    text = re.sub(r"\bi\s*/\s*o\b", "io", text, flags=re.I)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    pieces = {piece for word in _WORDS.findall(text.casefold()) for piece in (word, *word.split("_"))
              if len(piece) >= 2 and piece not in _PATH_STOP}
    return {piece[:-1] if len(piece) >= 5 and piece.endswith("s")
            and not piece.endswith(("ss", "us", "is")) else piece for piece in pieces}


def supporting_path(path: str) -> bool:
    file = PurePosixPath(path.casefold())
    return (any(part in _SUPPORT_DIRS for part in file.parts[:-1])
            or file.name.startswith("test_") or file.stem.endswith("_test") or file.suffix in _SUPPORT_SUFFIXES)


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
    def __init__(self, source: RepositorySource, *, max_parallel: int = 4, path_ranking: str = "source"):
        self.source = source
        self._limit = asyncio.Semaphore(max_parallel)
        self.path_ranking = path_ranking
        self._contents: dict[str, str | None] = {}
        self._catalog: dict[str, tuple[set[str], set[str]]] | None = None

    async def catalog(self) -> dict[str, tuple[set[str], set[str]]]:
        if self._catalog is None:
            manifest = await self.source.manifest()
            extract = {"legacy": query_terms, "components": identifier_terms, "source": source_terms}[self.path_ranking]
            source_ranking = self.path_ranking == "source"
            self._catalog = {}
            for path, entry in manifest.items():
                if safe_source_path(path) and _DISCOVERY_FILTER.should_include(GitHubFile(path, "blob", entry.size)):
                    file = PurePosixPath(path)
                    name = file.stem if source_ranking else file.name
                    location = str(file.parent) if source_ranking else path
                    self._catalog[path] = (extract(name), extract(location))
        return self._catalog

    async def _load_content(self, path: str) -> str | None:
        if path not in self._contents:
            async with self._limit:
                if path not in self._contents:
                    self._contents[path] = await asyncio.wait_for(self.source.read_file(path), 3.0)
        return self._contents[path]

    async def find_paths(self, query: str, skill: EvidenceSkill, indexed: list[dict], limit: int = 8,
                         *, scope: str = "", lookup_symbols: bool = True) -> list[str]:
        repo_name = self.source.repo.rsplit("/", 1)[-1]
        terms = source_terms(query) - source_terms(repo_name) if self.path_ranking == "source" else query_terms(query)
        if self.path_ranking == "components":
            terms = identifier_terms(query) - identifier_terms(repo_name)
        query_folded = query.lower()
        asks_support = terms & SUPPORT_TOPICS
        named_topics = {name.casefold() for name in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", query)} & terms
        known_paths = {item["file"] for item in indexed}
        lookup = getattr(self.source, "find_literal_paths", None)
        identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", query)
        names = [name for name in identifiers if "_" in name or re.search(r"[a-z][A-Z]", name)
                 or f"`{name}`" in query]
        symbol_paths = set(await lookup(list(dict.fromkeys(names))[:5])) if lookup_symbols and lookup and names else set()
        matches = []
        for path, (filename_terms, path_terms) in (await self.catalog()).items():
            if scope and path != scope and not path.startswith(scope + "/"):
                continue
            folded_path = path.lower()
            if self.path_ranking == "source":
                matching_name = terms & filename_terms
                score = (8 * len(matching_name) + 3 * len((terms & path_terms) - matching_name)
                         + 3 * len(named_topics & filename_terms))
                if path in symbol_paths:
                    score += 6
                if not asks_support and supporting_path(path):
                    score *= 0.45
                if skill.path_prefix and folded_path in _CI_FILES:
                    score += 20
            else:
                score = (7 if self.path_ranking == "components" else 3) * len(terms & filename_terms) + len(terms & path_terms)
                if path in symbol_paths:
                    score += 6
            literal = folded_path in query_folded and re.search(
                r"(?<![\w./-])" + re.escape(folded_path) + r"(?![\w./-])", query_folded)
            if literal:
                score += 20
            if skill.path_prefix and path.startswith(skill.path_prefix):
                score += 20 if self.path_ranking == "source" else 8
            if path in known_paths:
                score += 2
            if score:
                matches.append((score, path))
        return [path for _, path in sorted(matches, key=lambda match: (-match[0], match[1]))[:limit]]

    async def grep(self, literal: str, scope: str = "", limit: int = 10) -> list[GrepHit]:
        if not 2 <= len(literal.strip()) <= 100 or any(ord(char) < 32 for char in literal):
            raise ValueError("A literal must contain 2–100 printable characters")
        if scope and not safe_source_path(scope):
            raise ValueError("Invalid repository scope")
        catalog = await self.catalog()
        grep = getattr(self.source, "grep_literal", None)
        if grep:
            hits = await grep(literal, scope)
            return [hit for hit in hits if hit.path in catalog][:limit]

        # Remote-only repositories have no commit-scoped Git grep endpoint.
        paths = await self.find_paths(literal, EvidenceSkill(""), [], limit=6,
                                      scope=scope, lookup_symbols=False)
        if scope in catalog and scope not in paths:
            paths = [scope]
        contents = await asyncio.gather(*(self._load_content(path) for path in paths), return_exceptions=True)
        hits = []
        for path, content in zip(paths, contents):
            if isinstance(content, str):
                hits.extend(GrepHit(path, i, line[:320]) for i, line in enumerate(content.splitlines(), 1)
                            if literal.casefold() in line.casefold())
                if len(hits) >= limit:
                    break
        return hits[:limit]

    async def read(self, path: str, query: str, hints: list[dict],
                   *, lines: tuple[int, int] | None = None) -> ReadResult:
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
            content = await self._load_content(path)
        except Exception:
            return ReadResult(path, "unavailable", latency_ms=(time.perf_counter() - started) * 1000)
        if content is None:
            return ReadResult(path, "unreadable", latency_ms=(time.perf_counter() - started) * 1000)
        if not content.strip():
            return ReadResult(path, "empty", latency_ms=(time.perf_counter() - started) * 1000)

        if lines:
            first, last = lines
            source_lines = content.splitlines(keepends=True)
            last = min(last, len(source_lines))
            if first > last or first < 1 or last - first >= 45:
                return ReadResult(path, "invalid_range", latency_ms=(time.perf_counter() - started) * 1000)
            span = {"file": path, "content": "".join(source_lines[first-1:last]).rstrip("\r\n"),
                    "metadata": {"file": path, "start_line": first, "end_line": last,
                                 "commit": self.source.revision, "blob": entry.sha, "type": "source"}}
            return ReadResult(path, "ok", (span,), (time.perf_counter() - started) * 1000)

        terms = query_terms(query)
        hinted_lines = {line for item in hints if item["file"] == path
                        if isinstance(line := item.get("metadata", {}).get("start_line"), int)}
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
    ranges: dict[str, list[tuple[int, int]]] = {}
    live = [result.spans for result in results if result.spans]
    for depth in range(max(map(len, live), default=0)):
        for spans in live:
            if depth >= len(spans):
                continue
            item = spans[depth]
            meta = item["metadata"]
            label = f"E{len(evidence) + 1}"
            start = meta["start_line"]
            previous = ranges.setdefault(item["file"], [])
            if any(first <= start and meta["end_line"] <= last for first, last in previous):
                continue
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
            previous.append((start, end))
            content = "".join(lines).rstrip("\r\n")
            blocks.append(f"{prefix}-{end}\n{content}")
            evidence.append({**item, "id": label, "content": content,
                             "metadata": {**meta, "end_line": end}})
            max_bytes -= size
    return "\n\n".join(blocks), evidence
