"""Resumable E baseline: full repository index followed by one-shot retrieval."""
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
import shutil

from .data import digest, jobs, load_manifest, read_json, write_json
from .locking import exclusive_run
from .meter import Ledger
from .report import paired_bootstrap, percentile
from .runner import environment, freeze, launch, preflight, restore_seed, source_fingerprint, tree_hash


PROTOCOL_FILES = ("manifest.json", "references.json", "judge_prompt.txt", "formal_selection.json",
                  "evidence_labels.json")


def initialize_run(run, source_run):
    run, source_run = Path(run).resolve(), Path(source_run).resolve()
    run.mkdir(parents=True, exist_ok=True)
    for name in PROTOCOL_FILES:
        src, dst = source_run / name, run / name
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            raise ValueError(f"E protocol file changed: {name}")
        if not dst.exists():
            shutil.copyfile(src, dst)
    return run, source_run


def source_seeds(source_run):
    result = {}
    for job_file in (source_run / "seeds").glob("*/job.json"):
        payload = read_json(job_file)
        seed = job_file.parent
        if payload.get("kind") == "seed" and (seed / "sealed.json").exists():
            if tree_hash(seed / "data") != read_json(seed / "sealed.json")["data_hash"]:
                raise ValueError(f"Source seed changed: {seed}")
            result[(payload["repo"], payload["commit_id"])] = seed
    return result


@exclusive_run
def run_full_static(directory, source_directory, repos, rates, budget=15, timeout=600,
                    seed_timeout=21600, model=None):
    run, source_run = initialize_run(directory, source_directory)
    manifest = load_manifest(run / "manifest.json")
    info = preflight(model)
    if not (info["meter_supported"] and info["generation_key"] and info["embedding_key"] and info["judge_key"]):
        raise ValueError(f"E preflight failed: {info}")
    frozen = freeze(run, {"manifest_hash": manifest["manifest_hash"], "model": info,
        "rates": rates, "budget_cny": budget, "timeout_seconds": timeout,
        "judge_model": "gpt-5-2025-08-07", "experiment": "E_full_static_one_shot",
        "source_run": str(source_run), "source_fingerprint_lineage": read_json(source_run / "frozen.json")["source_fingerprint"]})
    ledger = Ledger(run / "usage.sqlite", budget)
    base = {"ledger": str(run / "usage.sqlite"), "budget_cny": budget, "rates": rates,
            "chat_config": read_json(source_run / "frozen.json")["configs"]["chat"], "model": info["model"]}
    selected = set(read_json(run / "formal_selection.json")["sample_ids"])
    samples = dict(manifest, samples=[s for s in manifest["samples"] if s["id"] in selected])
    expected = list(jobs(samples, "test", variants=("E",)))
    write_json(run / "schedule_test.json", expected)
    old_seeds = source_seeds(source_run)
    full_seeds = {}
    incomplete = []
    for repo, sha in sorted({(s["repo"], s["commit_id"]) for s in samples["samples"]}):
        old = old_seeds.get((repo, sha))
        if old is None:
            raise ValueError(f"Missing sealed source seed for {repo}")
        key = digest([repo, sha, frozen, "full-static-v1"])[:24]
        seed = run / "seeds" / key
        if not (seed / "sealed.json").exists():
            if not seed.exists():
                seed.mkdir(parents=True)
                shutil.copytree(old / "data", seed / "data")
            payload = dict(base, kind="full_seed", phase="full_seed", job_id=f"full_seed_{key}",
                           timeout_seconds=seed_timeout, repo=repo, commit_id=sha,
                           repo_root=str((Path(repos) / repo).resolve()))
            result = launch(payload, seed)
            write_json(seed / "record.json", {"repo": repo, "commit_id": sha, "result": result,
                "calls": ledger.records(payload["job_id"]), "source_fingerprint": frozen["source_fingerprint"]})
            if result["status"] == "completed":
                write_json(seed / "sealed.json", {"data_hash": tree_hash(seed / "data")})
        if not (seed / "sealed.json").exists():
            incomplete.append(repo)
        else:
            full_seeds[repo] = seed
    if incomplete:
        return {"phase": "full_seed", "completed": len(full_seeds), "incomplete": incomplete}
    for job in expected:
        work = run / "jobs" / job["job_id"]
        if (work / "record.json").exists():
            continue
        if work.exists():
            result = read_json(work / "result.json") if (work / "result.json").exists() else {
                "status": "interrupted", "answer": "", "error": "Previous process interrupted"}
        else:
            work.mkdir(parents=True)
            restore_seed(full_seeds[job["sample"]["repo"]], work)
            sample = job["sample"]
            payload = dict(base, kind="question", phase="test", timeout_seconds=timeout,
                           job_id=job["job_id"], variant="E", repo=sample["repo"],
                           commit_id=sample["commit_id"], question=sample["question"],
                           repo_root=str((Path(repos) / sample["repo"]).resolve()))
            result = launch(payload, work)
        write_json(work / "record.json", dict(job, result=result, calls=ledger.records(job["job_id"]),
                   manifest_hash=manifest["manifest_hash"], source_fingerprint=frozen["source_fingerprint"]))
        if (work / "job.json").exists() and (work / "data").exists():
            shutil.rmtree(work / "data")
        print(f"test {job['sample']['repo']} E: {result['status']}", flush=True)
    return {"phase": "test", "expected": len(expected),
            "recorded": sum((run / "jobs" / j["job_id"] / "record.json").exists() for j in expected)}


def comparison_report(directory, source_directory):
    run, source = Path(directory), Path(source_directory)
    e_schedule = read_json(run / "schedule_test.json")
    c_jobs = {j["sample"]["id"]: j for j in read_json(source / "schedule_test.json")
              if j["variant"] == "C" and j["repeat"] == 0}
    rows = []
    for job in e_schedule:
        sid = job["sample"]["id"]
        for variant, root, selected_job in (("E", run, job), ("C", source, c_jobs[sid])):
            path = root / "jobs" / selected_job["job_id"]
            record = read_json(path / "record.json")
            score = read_json(path / "score.json") if (path / "score.json").exists() else {"status": "missing"}
            rows.append({"repo": job["sample"]["repo"], "sample_id": sid, "variant": variant,
                         "repeat": 0, "status": record["result"]["status"], "score": score.get("total"),
                         "score_status": score["status"], "latency_ms": record["result"].get("latency_ms")})
    groups = {}
    for variant in ("C", "E"):
        subset = [r for r in rows if r["variant"] == variant]
        scored = [r for r in subset if r["score"] is not None]
        latencies = [r["latency_ms"] for r in subset if r["latency_ms"] is not None]
        groups[variant] = {"expected": len(subset), "statuses": dict(Counter(r["status"] for r in subset)),
                           "scored": len(scored), "score_mean": mean(r["score"] for r in scored) if scored else None,
                           "latency_p50_ms": percentile(latencies, .5), "latency_p95_ms": percentile(latencies, .95)}
    ready = all(r["status"] != "completed" or r["score_status"] == "scored" for r in rows)
    result = {"protocol": "C selective initial KB + JIT vs E full static index + one-shot retrieval",
              "ready_for_comparison": ready, "groups": groups,
              "comparison": paired_bootstrap(rows, "C", "E") if ready else {}}
    write_json(run / "reports" / "test" / "comparison_c_e.json", result)
    return result
