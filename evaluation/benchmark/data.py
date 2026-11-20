"""Dataset pinning and deterministic, reference-free generation manifests."""
import hashlib
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DATASET = "TIGER-Lab/SWE-QA-Pro-Bench"
UPSTREAM = "TIGER-AI-Lab/SWE-QA-Pro"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def load_manifest(path):
    manifest = read_json(path)
    if manifest["manifest_hash"] != digest({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("Manifest content hash mismatch")
    return manifest


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "RepoReaper-benchmark"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def download_dataset(directory):
    directory = Path(directory)
    info = json.loads(fetch(f"https://huggingface.co/api/datasets/{DATASET}"))
    revision = info["sha"]
    candidates = [r["rfilename"] for r in info["siblings"] if r["rfilename"].endswith(".jsonl")]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one benchmark JSONL, found {candidates}")
    raw = fetch(f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{candidates[0]}")
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    upstream = json.loads(fetch(f"https://api.github.com/repos/{UPSTREAM}/commits/main"))["sha"]
    prompt = fetch(f"https://raw.githubusercontent.com/{UPSTREAM}/{upstream}/eval/prompts/judge_prompt.txt")
    write_json(directory / "dataset.json", rows)
    (directory / "judge_prompt.txt").write_bytes(prompt)
    metadata = {"dataset": DATASET, "revision": revision, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
                "upstream_revision": upstream, "judge_prompt_sha256": hashlib.sha256(prompt).hexdigest(),
                "url": f"https://huggingface.co/datasets/{DATASET}/tree/{revision}"}
    write_json(directory / "provenance.json", metadata)
    return metadata


def prepare(rows, provenance, directory, per_repo=4, seed=42):
    if per_repo not in (2, 4):
        raise ValueError("formal samples per repository must be 2 or 4")
    directory = Path(directory)
    if (directory / "manifest.json").exists():
        raise ValueError("Manifest already exists; create a new run directory")
    groups = defaultdict(list)
    references = {}
    for row in rows:
        repo, sha, question = row["repo"], row["commit_id"], row["question"].strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid repository identifier")
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or not question or not row["answer"].strip():
            raise ValueError("Invalid commit/question/reference")
        sample_id = digest([repo, sha, question])
        if sample_id in references:
            raise ValueError("Duplicate benchmark question")
        references[sample_id] = row["answer"]
        # Whitelist fields: no reference answers or extra dataset fields cross
        # the worker boundary. qa_type/cluster are reporting metadata only.
        groups[repo].append({"id": sample_id, "repo": repo, "commit_id": sha,
                             "question": question, "qa_type": row.get("qa_type", {}),
                             "cluster": row.get("cluster", {})})
    samples = []
    for repo, records in sorted(groups.items()):
        if len(records) < per_repo + 1 or len({r["commit_id"] for r in records}) != 1:
            raise ValueError(f"Insufficient samples or multiple revisions: {repo}")
        ordered = sorted(records, key=lambda r: digest([seed, r["id"]]))
        for index, row in enumerate(ordered[:per_repo + 1]):
            samples.append(dict(row, split="dev" if index == 0 else "test",
                                evidence_review=index == 1, repeat=index == 1))
    selected_refs = {s["id"]: references[s["id"]] for s in samples}
    manifest = {"protocol": "SWE-QA-Pro fixed subset / custom RepoReaper Agent",
                "seed": seed, "per_repo": per_repo, "provenance": provenance, "samples": samples,
                "reference_hash": digest(selected_refs),
                "distribution": dict(Counter(str(s["qa_type"]) for s in samples if s["split"] == "test"))}
    manifest["manifest_hash"] = digest(manifest)
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "references.json", selected_refs)
    write_json(directory / "evidence_labels.json", {
        s["id"]: {"repo": s["repo"], "commit_id": s["commit_id"], "question": s["question"],
                  "required_files": [], "annotator": "", "verified_before_review": False}
        for s in samples if s["evidence_review"]})
    return manifest


def jobs(manifest, split, variants=("A", "B", "C", "D"), repeats=False):
    for sample in manifest["samples"]:
        if sample["split"] != split or (repeats and not sample["repeat"]):
            continue
        for variant in variants:
            if variant not in ("A", "B", "C", "D", "E"):
                raise ValueError("Unknown variant")
            for repeat in ((1, 2) if repeats else (0,)):
                if repeats and variant not in ("B", "C"):
                    continue
                yield {"job_id": f"{sample['id']}_{variant}_{repeat}", "sample": sample,
                       "variant": variant, "repeat": repeat}
