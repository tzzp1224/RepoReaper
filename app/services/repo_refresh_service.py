"""Validate GitHub push deliveries and refresh existing repository snapshots."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from contextlib import aclosing
from dataclasses import dataclass

from app.services.agent_service import agent_stream
from app.services.vector_service import store_manager
from app.storage.repo_refresh_store import RepoRefreshStore
from app.utils.session import generate_repo_session_id


logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 2 * 1024 * 1024
_REPO = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_DELIVERY = re.compile(r"[A-Za-z0-9-]{1,100}")


def webhook_enabled() -> bool:
    return bool(os.getenv("REPO_WEBHOOK_SECRET") and os.getenv("REPO_WEBHOOK_REPOSITORIES"))


@dataclass(frozen=True)
class PushDelivery:
    repo: str
    revision: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"


def parse_delivery(raw: bytes, signature: str, event: str, secret: str,
                   allowed_repos: set[str]) -> PushDelivery | None:
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise PermissionError("Invalid GitHub webhook signature")
    if event != "push":
        return None
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("repository"), dict):
        raise ValueError("Invalid push payload")
    repository = payload["repository"]
    name = repository.get("full_name")
    branch = repository.get("default_branch")
    revision = payload.get("after")
    if not isinstance(name, str) or not _REPO.fullmatch(name) or name.rsplit("/", 1)[-1] in {".", ".."}:
        raise ValueError("Invalid push repository")
    if (name.lower() not in allowed_repos or not isinstance(branch, str) or not branch
            or payload.get("ref") != f"refs/heads/{branch}" or payload.get("deleted")):
        return None
    if not isinstance(revision, str) or not _SHA.fullmatch(revision) or revision == "0" * 40:
        raise ValueError("Invalid push commit")
    return PushDelivery(name.lower(), revision)


class RepoRefreshService:
    def __init__(self, store: RepoRefreshStore | None = None):
        self.store = store or RepoRefreshStore()
        self.secret = os.environ["REPO_WEBHOOK_SECRET"]
        self.allowed_repos = {name.strip().lower() for name in os.environ["REPO_WEBHOOK_REPOSITORIES"].split(",")
                              if name.strip()}
        if not self.allowed_repos or not all(_REPO.fullmatch(repo) for repo in self.allowed_repos):
            raise ValueError("REPO_WEBHOOK_REPOSITORIES must contain GitHub owner/repo names")

    async def accept(self, *, raw: bytes, signature: str, event: str, delivery_id: str) -> bool:
        push = parse_delivery(raw, signature, event, self.secret, self.allowed_repos)
        if push is None:
            return False
        if not _DELIVERY.fullmatch(delivery_id):
            raise ValueError("Invalid GitHub delivery ID")
        session = store_manager.get_store(generate_repo_session_id(push.url))
        state = await asyncio.to_thread(session.load_context) or {}
        if not state.get("snapshot_index"):
            return False
        return await asyncio.to_thread(self.store.enqueue, delivery_id, push.repo, push.revision)

    async def worker(self):
        while True:
            try:
                job = await asyncio.to_thread(self.store.claim)
                if job:
                    await self._refresh(job)
                else:
                    await asyncio.sleep(2)
            except Exception:
                logger.exception("Repository refresh worker could not poll or record a job")
                await asyncio.sleep(5)

    async def _refresh(self, job: dict):
        url = f"https://github.com/{job['repo']}"
        session_id = generate_repo_session_id(url)

        async def analyze() -> str:
            state = await asyncio.to_thread(store_manager.get_store(session_id).load_context) or {}
            if not state.get("snapshot_index"):
                raise ValueError("Snapshot no longer available")
            language = state.get("report_language", "en")
            # The analyzer synchronizes the remote default branch. A delayed push only wakes it.
            async with aclosing(agent_stream(url, session_id, language=language)) as stream:
                async for raw in stream:
                    event = json.loads(raw)
                    if event.get("step") == "error":
                        raise RuntimeError("Snapshot analyzer failed")
                    if event.get("step") == "finish" and _SHA.fullmatch(event.get("commit", "")):
                        return event["commit"]
            raise RuntimeError("Snapshot analyzer ended without a published revision")

        try:
            revision = await asyncio.wait_for(analyze(), timeout=240)
        except asyncio.CancelledError:
            await asyncio.to_thread(self.store.finish, job, error="worker_cancelled")
            raise
        except Exception as exc:
            await asyncio.to_thread(self.store.finish, job, error=type(exc).__name__)
        else:
            await asyncio.to_thread(self.store.finish, job, published=revision)
