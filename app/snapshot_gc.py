"""Operator-driven garbage collection of retired snapshot vectors."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.core.config import vector_config
from app.storage.qdrant_store import close_shared_client, get_qdrant_factory
from app.storage.snapshot_leases import collection_lease
from app.utils.github_client import parse_repo_url
from app.utils.locking import AtomicJsonFileStore
from app.utils.repo_lock import RepoLock
from app.utils.session import generate_repo_lock_key


def retirement_plan(root: Path, repo_url: str, cutoff: float, keep: int):
    """Use every session's active manifest to protect shared vector points."""
    repo = tuple(part.lower() for part in parse_repo_url(repo_url))
    active: dict[str, set[str]] = {}
    retired: dict[str, set[str]] = {}
    affected: dict[Path, set[tuple[str, float]]] = {}
    for path in root.glob("*.json"):
        state = json.loads(path.read_text())
        manifest = state.get("snapshot_index") or {}
        if manifest:
            active.setdefault(manifest["namespace"], set()).update(
                doc_id for item in manifest["files"].values() for doc_id in item["docs"])
        by_collection: dict[str, list[dict]] = {}
        for item in state.get("retired_snapshots", []):
            owner = parse_repo_url(item.get("repo_url", state.get("repo_url", ""))) or ()
            if tuple(part.lower() for part in owner) == repo:
                by_collection.setdefault(item["namespace"], []).append(item)
        for namespace, rows in by_collection.items():
            rows.sort(key=lambda row: row["retired_at"], reverse=True)
            for offset, row in enumerate(rows):
                if offset < keep or row["retired_at"] >= cutoff:
                    active.setdefault(namespace, set()).update(row["document_ids"])
                else:
                    retired.setdefault(namespace, set()).update(row["document_ids"])
                    affected.setdefault(path, set()).add((namespace, row["retired_at"]))
    return {name: sorted(ids - active.get(name, set())) for name, ids in retired.items()}, affected


async def collect(repo_url: str, *, apply: bool = False, grace_seconds: float = 86400, keep: int = 2):
    if not parse_repo_url(repo_url) or grace_seconds < 0 or keep < 0:
        raise ValueError("Invalid repository or retention policy")
    root = Path(vector_config.context_dir)
    cutoff = time.time() - grace_seconds
    report = {}
    async with RepoLock.acquire(generate_repo_lock_key(repo_url)):
        plan, _ = await asyncio.to_thread(retirement_plan, root, repo_url, cutoff, keep)
        for namespace, document_ids in plan.items():
            if not apply:
                report[namespace] = {"candidates": len(document_ids), "status": "dry_run"}
                continue
            try:
                async with collection_lease(root, namespace, exclusive=True, timeout=0.25):
                    # Readers were excluded after the first pass. Recheck membership before deletion.
                    plan, affected = await asyncio.to_thread(retirement_plan, root, repo_url, cutoff, keep)
                    document_ids = plan.get(namespace, [])
                    await get_qdrant_factory().create(namespace).delete_documents(document_ids)
                    for path, items in affected.items():
                        def discard(state: dict):
                            state["retired_snapshots"] = [row for row in state.get("retired_snapshots", [])
                                if row["namespace"] != namespace or (namespace, row["retired_at"]) not in items]

                        if not await asyncio.to_thread(AtomicJsonFileStore(str(path)).update, discard,
                                                       op_name="Record collected snapshots"):
                            raise RuntimeError("Could not record retired snapshots after collection")
                    report[namespace] = {"candidates": len(document_ids), "status": "collected"}
            except TimeoutError:
                report[namespace] = {"candidates": len(document_ids), "status": "in_flight"}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_url")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run():
        try:
            print(json.dumps(await collect(args.repo_url, apply=args.apply), indent=2))
        finally:
            await close_shared_client()

    asyncio.run(run())


if __name__ == "__main__":
    main()
