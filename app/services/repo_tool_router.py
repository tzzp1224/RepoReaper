"""Run a bounded repository investigation using Jev as the tool classifier."""

import asyncio
import re
import time
from dataclasses import dataclass

from app.services import jev_gate
from app.services.repo_explorer import Exploration, SemanticSearch
from app.services.repository_evidence import RepositoryEvidence, source_terms


MAX_ROUNDS = 4
MAX_TARGETS = 8
_ACTIONS = {
    "search_semantic": "Find relevant locations in the index by behavior or concept when exact names are uncertain.",
    "grep": "Locate uses or definitions of a specific literal, identifier, or config key.",
    "read_spans": "Read an unread candidate file or exact line window to verify implementation details.",
    "answer": "Answer from the verified spans already read when they cover the requested details.",
}


@dataclass(frozen=True)
class ReadTarget:
    path: str
    line: int | None = None

    def description(self) -> str:
        return self.path if self.line is None else f"{self.path} around line {self.line}"


def _grep_literals(question: str, repo: str) -> list[str]:
    quoted = re.findall(r"`([^`\n]{2,80})`|\"([^\"\n]{2,80})\"", question)
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,79}", question)
    identifiers = [word for word in words if "_" in word or re.search(r"[a-z][A-Z]", word) or word.isupper()]
    topics = source_terms(question) - source_terms(repo.rsplit("/", 1)[-1])
    topical = [word for word in words if source_terms(word) & topics and len(word) >= 5]
    options = [next(value for value in pair if value) for pair in quoted] + identifiers + topical
    seen = {}
    for literal in options:
        if len(literal.strip()) >= 2 and all(ord(char) >= 32 for char in literal):
            seen.setdefault(literal.casefold(), literal)
    return list(seen.values())[:6]


async def investigate_with_jev(question: str, tools: RepositoryEvidence, paths: list[str],
                               semantic_search: SemanticSearch | None, *, timeout: float,
                               choice_timeout: float, shadow: bool = False) -> Exploration:
    outcome = Exploration([], [])
    deadline = time.perf_counter() + timeout
    catalog = await tools.catalog()
    candidates = [ReadTarget(path) for path in paths if path in catalog]
    literals = _grep_literals(question, tools.source.repo)
    attempted_reads: set[ReadTarget] = set()
    observations = []

    def uncovered(target: ReadTarget) -> bool:
        if target in attempted_reads:
            return False
        if target.line is None:
            return all(read.path != target.path for read in outcome.reads)
        return not any(span["metadata"]["start_line"] <= target.line <= span["metadata"]["end_line"]
                       for read in outcome.reads if read.path == target.path for span in read.spans)

    for _ in range(MAX_ROUNDS):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        targets = {f"file_{i}": target for i, target in enumerate(
            [target for target in dict.fromkeys(candidates) if uncovered(target)][:MAX_TARGETS], 1)}
        grep_options = {f"literal_{i}": literal for i, literal in enumerate(literals, 1)}
        available = {"search_semantic": semantic_search is not None, "grep": bool(grep_options),
                     "read_spans": bool(targets), "answer": bool(outcome.reads)}
        # A final search without time for a source read cannot improve the answer.
        if outcome.rounds == MAX_ROUNDS - 1:
            available["search_semantic"] = available["grep"] = False
        actions = {name: text for name, text in _ACTIONS.items() if available[name]}
        if not actions or list(actions) == ["answer"]:
            outcome.status = "ready" if outcome.reads else "no_candidates"
            break
        state = {"question": question[:1000], "commit": tools.source.revision,
                 "verified_spans": [
                     {"path": read.path, "start": span["metadata"]["start_line"],
                      "end": span["metadata"]["end_line"], "symbol": span["metadata"].get("name", "")[:100]}
                     for read in outcome.reads for span in read.spans
                 ][:10], "tool_attempts": observations}
        arguments = {"grep": grep_options,
                     "read_spans": {key: target.description()[:512] for key, target in targets.items()}}
        outcome.rounds += 1
        decision = await jev_gate.choose_tool(state, actions, {k: v for k, v in arguments.items() if k in actions},
                                              timeout=min(choice_timeout, remaining), shadow=shadow)
        if shadow:
            outcome.status = "shadow"
            break
        if decision is None:
            outcome.status = "router_unavailable"
            break
        action = decision.action
        if action == "answer":
            outcome.status = "ready"
            break

        started = time.perf_counter()
        status, path, literal, locations = "ok", None, None, []
        budget = max(0, min(2.5, deadline - started))
        try:
            if action == "search_semantic":
                search, semantic_search = semantic_search, None
                hits = await asyncio.wait_for(search(question[:1000]), budget)
                for hit in hits[:6]:
                    line = hit.get("metadata", {}).get("start_line")
                    locations.append(ReadTarget(hit["file"], line if type(line) is int and line > 0 else None))
            elif action == "grep":
                literal = grep_options[decision.argument]
                literals.remove(literal)
                hits = await asyncio.wait_for(tools.grep(literal), budget)
                locations = [ReadTarget(hit.path, hit.line) for hit in hits[:6]]
            else:
                target = targets[decision.argument]
                path = target.path
                attempted_reads.add(target)
                lines = None if target.line is None else (max(1, target.line-8), target.line+23)
                result = await asyncio.wait_for(tools.read(path, question, [], lines=lines), budget)
                status = result.status
                if result.spans:
                    outcome.reads.append(result)
        except Exception as exc:
            status = type(exc).__name__
        locations = [target for target in locations if target.path in catalog]
        if action != "read_spans" and status == "ok" and not locations:
            status = "empty"
        candidates = locations + candidates
        observations.append({"action": action, "status": status, "path": path,
                             "literal": literal,
                             "matches": [target.description()[:256] for target in locations[:4]]})
        outcome.calls.append({"tool": action, "path": path, "status": status, "commit": tools.source.revision,
                              "success": status in {"ok", "empty"}, "latency_ms": (time.perf_counter()-started)*1000})
    return outcome
