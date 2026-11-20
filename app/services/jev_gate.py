"""Optional TypeSafe decision gate for ranking bounded source-read batches."""

import asyncio
import os
import time

import httpx

from app.services.tracing_service import tracing_service


_shadow_tasks: set[asyncio.Task] = set()


def observe_batch(query: str, current_paths: list[str], batches: dict[str, list[str]]) -> None:
    if os.getenv("TYPESAFE_API_KEY"):
        task = asyncio.create_task(choose_batch(query, current_paths, batches))
        _shadow_tasks.add(task)
        task.add_done_callback(_shadow_tasks.discard)


async def choose_batch(query: str, current_paths: list[str], batches: dict[str, list[str]]) -> str | None:
    key = os.getenv("TYPESAFE_API_KEY")
    if not key:
        return None
    started = time.perf_counter()
    decision = None
    metrics = {}
    async def request():
        async with httpx.AsyncClient() as client:
            return await client.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.getenv("JEV_MODEL", "jev-latest"),
                    "state": {"question": query[:1000], "current_paths": current_paths,
                              "candidate_batches": batches},
                    "questions": {"batch": {
                        "type": "choice",
                        "instructions": "Which batch is most likely to add specific evidence for this question?",
                        "criteria": {**{name: f"Read {', '.join(paths)}" for name, paths in batches.items()},
                                     "none": "The listed candidates are unlikely to answer the question"},
                    }},
                },
            )

    try:
        response = await asyncio.wait_for(request(), timeout=0.45)
        response.raise_for_status()
        payload = response.json()
        answer = payload["answers"]["batch"]
        choice = answer["choice"]
        probability = float(answer["probabilities"][choice])
        confidence = float(answer["confidence"])
        metrics = {"choice": choice, "probability": probability, "confidence": confidence,
                   "usage": payload.get("usage", {})}
        if choice in batches and 0.7 <= probability <= 1 and 0.5 <= confidence <= 1:
            decision = choice
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError, KeyError, AttributeError):
        metrics = {"fallback": "service_error"}
    mode = os.getenv("JEV_MODE", "off").lower()
    tracing_service.add_event("jev_decision", {
        "mode": mode, "baseline_batch": next(iter(batches), None), **metrics,
        "proposed_batch": decision, "used_batch": decision if mode == "active" else None,
        "latency_ms": (time.perf_counter() - started) * 1000,
    })
    return decision
