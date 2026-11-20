"""Pinned upstream five-axis rubric, with explicit no-score terminal records."""
import asyncio
import hashlib
import json
from pathlib import Path

from .data import read_json, write_json, load_manifest, digest
from .meter import Ledger, Meter
from .runner import environment, source_fingerprint
from .locking import exclusive_run

AXES = ("correctness", "completeness", "relevance", "clarity", "reasoning")


def parse_scores(text):
    data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    if any(type(data.get(k)) is not int or not 1 <= data[k] <= 10 for k in AXES):
        raise ValueError("Judge scores must be five integers in [1,10]")
    return {k: data[k] for k in AXES}


@exclusive_run
async def score(directory, split="dev", retry_failed=False):
    from openai import AsyncOpenAI
    run = Path(directory).resolve()
    frozen = read_json(run / "frozen.json")
    if frozen["source_fingerprint"] != source_fingerprint():
        raise ValueError("Source changed since generation; use a new run")
    references = read_json(run / "references.json")
    manifest = load_manifest(run / "manifest.json")
    if digest(references) != manifest["reference_hash"]:
        raise ValueError("Reference answers changed after preparation")
    prompt = (run / "judge_prompt.txt").read_text()
    expected_hash = manifest["provenance"]["judge_prompt_sha256"]
    if hashlib.sha256(prompt.encode()).hexdigest() != expected_hash:
        raise ValueError("Judge prompt differs from pinned upstream rubric")
    env = environment()
    if not env.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY missing")
    client = AsyncOpenAI(api_key=env["OPENAI_API_KEY"], base_url=env.get("OPENAI_BASE_URL") or None,
                         max_retries=0, timeout=120)
    ledger = Ledger(run / "usage.sqlite", frozen["budget_cny"])
    schedules = [run / f"schedule_{split}.json"]
    if split == "test" and (run / "schedule_repeat.json").exists():
        schedules.append(run / "schedule_repeat.json")
    try:
        for schedule in schedules:
            for job in read_json(schedule):
                path = run / "jobs" / job["job_id"]
                output = path / "score.json"
                if not (path / "record.json").exists():
                    # Generation may be interrupted. Keep the job in the report
                    # denominator and allow a later resumed generation to be scored.
                    continue
                if output.exists() and (not retry_failed or read_json(output)["status"] != "judge_error"):
                    continue
                record = read_json(path / "record.json")
                answer = record["result"].get("answer", "").strip()
                if record["result"]["status"] != "completed" or not answer:
                    write_json(output, {"status": "no_answer", "scores": None, "job_id": job["job_id"]})
                    continue
                meter = Meter(ledger, job["job_id"], "judge_" + split, frozen["rates"])
                request = meter.wrap(client.responses.create, "judge")
                formatted = prompt.format(question=job["sample"]["question"],
                                          reference=references[job["sample"]["id"]], candidate=answer)
                try:
                    response = await request(model=frozen["judge_model"],
                        input=[{"role": "system", "content": "You are a helpful assistant."},
                               {"role": "user", "content": formatted}],
                        reasoning={"effort": "low"}, text={"verbosity": "low"}, max_output_tokens=4096)
                    scores = parse_scores(response.output_text)
                    result = {"status": "scored", "scores": scores, "total": sum(scores.values()),
                              "judge_model": frozen["judge_model"], "response_id": response.id}
                except Exception as exc:
                    result = {"status": "judge_error", "scores": None, "error": type(exc).__name__}
                result["job_id"] = job["job_id"]
                write_json(output, result)
    finally:
        await client.close()


@exclusive_run
def select_formal(directory):
    """Select 104 or 52 before test generation using measured pilot costs only."""
    from collections import defaultdict
    from statistics import mean
    run = Path(directory).resolve()
    if (run / "formal_selection.json").exists():
        return read_json(run / "formal_selection.json")
    frozen = read_json(run / "frozen.json")
    manifest = load_manifest(run / "manifest.json")
    dev_jobs = read_json(run / "schedule_dev.json")
    costs = defaultdict(float)
    records = Ledger(run / "usage.sqlite", frozen["budget_cny"]).records()
    for row in records:
        costs[row["job"]] += row["cost_cny"]
    for job in dev_jobs:
        path = run / "jobs" / job["job_id"]
        if read_json(path / "record.json")["result"]["status"] != "completed":
            raise ValueError("Resolve pilot generation failures before formal evaluation")
        if read_json(path / "score.json")["status"] != "scored":
            raise ValueError("Complete independent pilot judging before formal evaluation")
    groups = defaultdict(list)
    for sample in manifest["samples"]:
        if sample["split"] == "test":
            groups[sample["repo"]].append(sample)
    already = sum(row["cost_cny"] for row in records)
    # 50% headroom; repeats are 26 repositories x B/C x two extra runs.
    unit = mean(costs[j["job_id"]] for j in dev_jobs)
    projected = {n: already + 1.5 * unit * len(groups) * (4 * n + 4) for n in (4, 2)}
    n = 4 if manifest["per_repo"] == 4 and projected[4] <= frozen["budget_cny"] else 2
    if projected[n] > frozen["budget_cny"]:
        raise ValueError("Even 52 questions exceed projected budget; no formal requests launched")
    chosen = [s["id"] for rows in groups.values() for s in rows[:n]]
    result = {"manifest_hash": manifest["manifest_hash"], "per_repo": n,
              "sample_ids": chosen, "projected_cny": projected[n], "pilot_cost_cny": already,
              "headroom_factor": 1.5, "selection_reason": "pre-test pilot cost projection"}
    write_json(run / "formal_selection.json", result)
    return result
