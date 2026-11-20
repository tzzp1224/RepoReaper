"""Durable pre-request budget reservations and per-attempt SDK usage accounting.

Unknown usage retains its conservative reservation, including interrupted jobs.
No background retries are hidden in the SDK. Application retries get new rows.
"""
import hashlib
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path, limit=450.0):
        self.path, self.limit = str(path), limit
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, job TEXT, phase TEXT, "
                       "reserved REAL, charged REAL, record TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, job, phase, amount, record):
        if amount < 0 or not math.isfinite(amount):
            raise ValueError("Nonnegative finite verified pricing is required")
        call_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COALESCE(SUM(charged),0) FROM calls").fetchone()[0]
            if used + amount > self.limit:
                raise BudgetExceeded(f"Budget reservation would exceed {self.limit} CNY")
            record.update(status="reserved", usage_kind="upper_bound", cost_cny=amount)
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?)",
                       (call_id, job, phase, amount, amount, json.dumps(record)))
        return call_id

    def finish(self, call_id, record, actual_cost=None):
        with self.connect() as db:
            reserved, original = db.execute("SELECT reserved,record FROM calls WHERE id=?", (call_id,)).fetchone()
            merged = json.loads(original)
            merged.update(record)
            charged = reserved if actual_cost is None else actual_cost
            merged["cost_cny"] = charged
            db.execute("UPDATE calls SET charged=?,record=? WHERE id=?", (charged, json.dumps(merged), call_id))

    def records(self, job=None):
        with self.connect() as db:
            rows = db.execute("SELECT id,job,phase,record FROM calls" + (" WHERE job=?" if job else ""),
                              (job,) if job else ()).fetchall()
        return [dict(json.loads(r), call_id=i, job=j, phase=p) for i, j, p, r in rows]


class Meter:
    def __init__(self, ledger, job, phase, rates):
        self.ledger, self.job, self.phase, self.rates = ledger, job, phase, rates

    def wrap(self, create, kind):
        async def measured(**kwargs):
            rates = self.rates[kind]
            if not rates.get("source") or not rates.get("verified_at"):
                raise ValueError("Pricing source and verification date are required")
            for key in ("input_cny_per_million", "output_cny_per_million"):
                if type(rates.get(key)) not in (int, float) or rates[key] < 0 or not math.isfinite(rates[key]):
                    raise ValueError("Missing or invalid pricing")
            payload = kwargs.get("messages", kwargs.get("input", []))
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            upper_input = len(serialized.encode()) + 4096
            upper_output = kwargs.get("max_tokens", kwargs.get("max_output_tokens", 4096)) if kind != "embedding" else 0
            reserve = (upper_input * rates["input_cny_per_million"] +
                       upper_output * rates.get("output_cny_per_million", 0)) / 1e6
            call_id = self.ledger.reserve(self.job, self.phase, reserve, {
                "kind": kind, "requested_model": kwargs.get("model"),
                "prompt_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
                "request_config": {k: v for k, v in kwargs.items() if k not in ("messages", "input")},
                "input_tokens_upper_bound": upper_input, "output_tokens_upper_bound": upper_output})
            start = time.perf_counter()
            usage = None
            response_id = returned_model = None
            output_chars = 0
            finalized = False

            def observe(response):
                nonlocal usage, response_id, returned_model, output_chars
                response_id = getattr(response, "id", response_id)
                returned_model = getattr(response, "model", returned_model)
                usage = getattr(response, "usage", None) or usage
                for choice in getattr(response, "choices", []) or []:
                    message = getattr(choice, "delta", None) or getattr(choice, "message", None)
                    output_chars += len(getattr(message, "content", "") or "")

            def finish(status):
                nonlocal finalized
                if finalized:
                    return
                finalized = True
                data = {"status": status, "response_id": response_id, "returned_model": returned_model,
                        "latency_ms": (time.perf_counter() - start) * 1000, "output_chars": output_chars}
                cost = None
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
                    output_tokens = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0))
                    if input_tokens is not None:
                        data.update(input_tokens=input_tokens, output_tokens=output_tokens, usage_kind="actual")
                        cost = (input_tokens * rates["input_cny_per_million"] +
                                output_tokens * rates.get("output_cny_per_million", 0)) / 1e6
                self.ledger.finish(call_id, data, cost)

            try:
                if kwargs.get("stream"):
                    kwargs["stream_options"] = {"include_usage": True}
                response = await create(**kwargs)
                if kwargs.get("stream"):
                    async def stream():
                        status = "interrupted"
                        try:
                            async for item in response:
                                observe(item)
                                yield item
                            status = "completed"
                        finally:
                            finish(status)
                            await response.close()
                    return stream()
                observe(response)
                finish("completed")
                return response
            except BaseException:
                finish("error")
                raise
        return measured
