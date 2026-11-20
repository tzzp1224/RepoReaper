"""Serial orchestrator with immutable run identities, isolated seeds and resume."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .data import digest, jobs, read_json, write_json, load_manifest
from .meter import Ledger
from .locking import exclusive_run

ROOT = Path(__file__).resolve().parents[2]


def source_fingerprint():
    paths = sorted(p for folder in ("app", "evaluation/benchmark")
                   for p in (ROOT / folder).rglob("*.py"))
    return digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def environment():
    from dotenv import dotenv_values
    values = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    values.update(os.environ)
    values.update(PYTHONPATH=str(ROOT), PYTHONHASHSEED="42", LANGFUSE_ENABLED="false",
                  AUTO_EVAL_ENABLED="false", PYTHON_DOTENV_DISABLED="1")
    return values


def preflight(model=None):
    env = environment()
    provider = env.get("LLM_PROVIDER", "deepseek")
    return {"provider": provider, "model": model or env.get("MODEL_NAME") or
            {"deepseek": "deepseek-flash", "openai": "gpt-4o-mini"}.get(provider),
            "generation_key": bool(env.get(provider.upper() + "_API_KEY")),
            "embedding_key": bool(env.get("SILICON_API_KEY")),
            "judge_key": bool(env.get("OPENAI_API_KEY")),
            "meter_supported": provider in ("deepseek", "openai"),
            "endpoint_hashes": {k: digest(env.get(k, "")) for k in ("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL")},
            "llm_defaults": {k: env.get(k) for k in ("LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TIMEOUT")}}


def clone_repos(manifest, root):
    root = Path(root)
    for repo, sha in sorted({(s["repo"], s["commit_id"]) for s in manifest["samples"]}):
        destination = root / repo
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            subprocess.run(["git", "init", "--bare", str(destination)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(destination), "remote", "add", "origin",
                            f"https://github.com/{repo}.git"], check=True)
        subprocess.run(["git", "-C", str(destination), "fetch", "--depth=1", "origin", sha], check=True, timeout=600)
        # Materialize every blob now: evaluation must not perform lazy network fetches.
        listing = subprocess.check_output(["git", "-C", str(destination), "ls-tree", "-rlz", sha])
        blobs = [r.split(b"\t", 1)[0].split()[2] for r in listing.split(b"\0") if r and b" blob " in r]
        subprocess.run(["git", "-C", str(destination), "cat-file", "--batch"],
                       input=b"\n".join(blobs) + b"\n", stdout=subprocess.DEVNULL, check=True, timeout=1200)


def tree_hash(directory):
    return digest({str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(Path(directory).rglob("*")) if p.is_file() and p.name != ".lock"})


def restore_seed(seed_dir, work):
    if tree_hash(seed_dir / "data") != read_json(seed_dir / "sealed.json")["data_hash"]:
        raise ValueError("Initial index changed after sealing")
    shutil.copytree(seed_dir / "data", work / "data")


def freeze(run, config):
    path = run / "frozen.json"
    packages = {}
    for package in ("openai", "qdrant-client", "rank-bm25", "httpx", "tenacity", "numpy"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    frozen = dict(config, source_fingerprint=source_fingerprint(), runtime={"python": sys.version, "packages": packages},
                  project_commit=subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip())
    if path.exists() and read_json(path) != frozen:
        raise ValueError("Run configuration/source changed; use a new run directory")
    write_json(path, frozen)
    return frozen


def launch(payload, work):
    work.mkdir(parents=True, exist_ok=True)
    job_file, output = work / "job.json", work / "result.json"
    if "answer" in payload or "references" in payload:
        raise ValueError("Reference leakage into generation worker")
    write_json(job_file, payload)
    env = environment()
    # Disable Git's promisor remote fetches during all snapshot reads.
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    with (work / "worker.log").open("w") as log:
        started = time.perf_counter()
        try:
            subprocess.run([sys.executable, "-m", "evaluation.benchmark.worker", str(job_file), str(output)],
                           cwd=work, env=env, stdout=log, stderr=log,
                           timeout=payload["timeout_seconds"] + 30, check=True)
        except subprocess.TimeoutExpired:
            write_json(output, {"status": "timeout", "answer": "", "error": "Process time limit exceeded"})
        except subprocess.CalledProcessError:
            if not output.exists():
                write_json(output, {"status": "error", "answer": "", "error": "Worker process exited"})
    result = read_json(output)
    result["latency_ms"] = (time.perf_counter() - started) * 1000
    write_json(output, result)
    return result


@exclusive_run
def run_jobs(directory, repos, rates, split="dev", repeats=False, budget=450, timeout=600, model=None):
    run = Path(directory).resolve()
    manifest = load_manifest(run / "manifest.json")
    info = preflight(model)
    if not (info["meter_supported"] and info["generation_key"] and info["embedding_key"]):
        raise ValueError(f"Generation preflight failed: {info}")
    if not info["judge_key"]:
        raise ValueError("OPENAI_API_KEY is missing: configure independent judging before paid experiments")
    import math
    for kind in ("generation", "embedding", "judge"):
        rate = rates[kind]
        if not rate.get("source") or not rate.get("verified_at"):
            raise ValueError(f"Unverified pricing for {kind}")
        if any(type(rate.get(k)) not in (int, float) or not math.isfinite(rate[k]) or rate[k] < 0
               for k in ("input_cny_per_million", "output_cny_per_million")):
            raise ValueError(f"Invalid pricing for {kind}")
    if rates["generation"].get("model") and rates["generation"]["model"] != info["model"]:
        raise ValueError("Generation pricing model does not match --model / MODEL_NAME")
    # Import application configs only in a subprocess to avoid creating runtime
    # state/log files in the user's checkout.
    code = ("import json; from dataclasses import asdict; from app.core.config import agent_config,vector_config; "
            "from app.services.chat_service import chat_config; "
            "print('CONFIG='+json.dumps({'agent':asdict(agent_config),'vector':asdict(vector_config),'chat':asdict(chat_config)}))")
    scratch = run / "config_probe"
    scratch.mkdir(exist_ok=True)
    probe_env = environment()
    probe_env["RUNTIME_TRACE_DB_PATH"] = str(scratch / "runtime.db")
    probe = subprocess.check_output([sys.executable, "-c", code], cwd=scratch, env=probe_env, text=True)
    configs = json.loads(next(s[7:] for s in probe.splitlines() if s.startswith("CONFIG=")))
    frozen = freeze(run, {"manifest_hash": manifest["manifest_hash"], "model": info,
                          "configs": configs, "rates": rates, "budget_cny": budget,
                          "timeout_seconds": timeout, "judge_model": "gpt-5-2025-08-07"})
    ledger = Ledger(run / "usage.sqlite", budget)
    base = {"ledger": str(run / "usage.sqlite"), "budget_cny": budget,
            "rates": rates, "timeout_seconds": timeout, "chat_config": configs["chat"], "model": info["model"]}
    selected_manifest = manifest
    if split == "test":
        selection = read_json(run / "formal_selection.json")
        if selection["manifest_hash"] != manifest["manifest_hash"]:
            raise ValueError("Formal selection manifest mismatch")
        selected_manifest = dict(manifest, samples=[s for s in manifest["samples"] if s["id"] in selection["sample_ids"]])
    expected = list(jobs(selected_manifest, split, repeats=repeats))
    phase = "repeat" if repeats else split
    # Publish the denominator before any model request, including seed failures.
    write_json(run / f"schedule_{phase}.json", expected)
    seed_by_repo = {}
    seed_failures = {}
    for repo, sha in sorted({(s["repo"], s["commit_id"]) for s in manifest["samples"]}):
        seed_key = digest([repo, sha, frozen])[:24]
        seed_dir = run / "seeds" / seed_key
        if not (seed_dir / "sealed.json").exists():
            if seed_dir.exists():
                # Preserve failed/interrupted attempts without silently spending again.
                result = read_json(seed_dir / "result.json") if (seed_dir / "result.json").exists() else {
                    "status": "interrupted", "error": "Seed process interrupted"}
                write_json(seed_dir / "result.json", result)
            else:
                payload = dict(base, job_id=f"seed_{seed_key}", kind="seed", phase="seed", repo=repo,
                               commit_id=sha, repo_root=str((Path(repos) / repo).resolve()))
                result = launch(payload, seed_dir)
            if result["status"] != "completed":
                seed_failures[repo] = str(seed_dir / "result.json")
                continue
            write_json(seed_dir / "sealed.json", {"data_hash": tree_hash(seed_dir / "data")})
        if tree_hash(seed_dir / "data") != read_json(seed_dir / "sealed.json")["data_hash"]:
            raise ValueError("Initial index changed after sealing")
        seed_by_repo[repo] = seed_dir
    for job in expected:
        sample = job["sample"]
        work = run / "jobs" / job["job_id"]
        if (work / "record.json").exists():
            continue
        if work.exists():
            # Preserve accounting for interrupted attempts; do not quietly rerun.
            result = read_json(work / "result.json") if (work / "result.json").exists() else {
                "status": "interrupted", "answer": "", "error": "Previous process interrupted"}
        else:
            work.mkdir(parents=True)
            if job["variant"] != "A" and sample["repo"] in seed_failures:
                result = {"status": "seed_error", "answer": "", "error": seed_failures[sample["repo"]]}
            else:
                if job["variant"] != "A":
                    restore_seed(seed_by_repo[sample["repo"]], work)
                payload = dict(base, kind="question", job_id=job["job_id"], phase=phase,
                               variant=job["variant"], repo=sample["repo"], commit_id=sample["commit_id"],
                               question=sample["question"], repo_root=str((Path(repos) / sample["repo"]).resolve()))
                result = launch(payload, work)
        record = dict(job, result=result, calls=ledger.records(job["job_id"]),
                      manifest_hash=manifest["manifest_hash"], source_fingerprint=frozen["source_fingerprint"])
        write_json(work / "record.json", record)
        # These are disposable copies created above. Preserve immutable seeds,
        # exact evidence and traces, without retaining hundreds of Qdrant copies.
        if (work / "job.json").exists() and (work / "data").exists():
            shutil.rmtree(work / "data")
        print(f"{phase} {sample['repo']} {job['variant']} #{job['repeat']}: {result['status']}", flush=True)
    return {"expected": len(expected), "recorded": sum((run / "jobs" / j["job_id"] / "record.json").exists() for j in expected)}
