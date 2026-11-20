"""Coverage-aware comparisons, repository bootstrap, and human review exports."""
import csv
import html
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .data import read_json, write_json, load_manifest
from .meter import Ledger


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    low = int(position)
    return values[low] + (values[min(low + 1, len(values)-1)] - values[low]) * (position-low)


def paired_bootstrap(rows, treatment, control, seed=42):
    paired = defaultdict(dict)
    for row in rows:
        if row["repeat"] == 0 and row["score"] is not None:
            paired[(row["repo"], row["sample_id"])][row["variant"]] = row["score"]
    clusters = defaultdict(list)
    for (repo, _), scores in paired.items():
        if treatment in scores and control in scores:
            clusters[repo].append(scores[treatment] - scores[control])
    if not clusters:
        return {"paired_questions": 0, "delta": None, "ci95": None}
    rng = random.Random(seed)
    groups = list(clusters.values())
    boot = [mean(v for group in rng.choices(groups, k=len(groups)) for v in group) for _ in range(2000)]
    return {"paired_questions": sum(map(len, groups)), "repositories": len(groups),
            "delta": mean(v for group in groups for v in group),
            "ci95": [percentile(boot, .025), percentile(boot, .975)]}


def generate_report(directory, split="test"):
    run = Path(directory).resolve()
    manifest = load_manifest(run / "manifest.json")
    ledger = Ledger(run / "usage.sqlite")
    calls = ledger.records()
    by_job = defaultdict(list)
    for call in calls:
        by_job[call["job"]].append(call)
    schedule = read_json(run / f"schedule_{split}.json")
    if split == "test" and (run / "schedule_repeat.json").exists():
        schedule += read_json(run / "schedule_repeat.json")
    rows = []
    for job in schedule:
        path = run / "jobs" / job["job_id"]
        result = read_json(path / "record.json")["result"] if (path / "record.json").exists() else {"status": "missing"}
        score = read_json(path / "score.json") if (path / "score.json").exists() else {"status": "missing"}
        job_calls = by_job[job["job_id"]]
        inference_calls = [c for c in job_calls if not c["phase"].startswith("judge_")]
        rows.append({"job_id": job["job_id"], "sample_id": job["sample"]["id"], "repo": job["sample"]["repo"],
            "variant": job["variant"], "repeat": job["repeat"], "status": result["status"],
            "score_status": score["status"], "score": score.get("total"), "axes": score.get("scores"),
            "latency_ms": result.get("latency_ms"), "jit_rounds": result.get("jit_rounds", 0),
            "tool_failures": sum(not c["success"] for c in result.get("tool_calls", [])),
            "read_files": len({r["path"] for r in result.get("reads", [])}),
            "llm_attempts": sum(c["kind"] == "generation" for c in inference_calls),
            "cost_cny": sum(c["cost_cny"] for c in inference_calls),
            "judge_cost_cny": sum(c["cost_cny"] for c in job_calls if c["phase"].startswith("judge_")),
            "actual_usage_calls": sum(c["usage_kind"] == "actual" for c in inference_calls),
            "unknown_usage_calls": sum(c["usage_kind"] != "actual" for c in inference_calls),
            "input_tokens_actual": sum(c.get("input_tokens", 0) for c in inference_calls),
            "output_tokens_actual": sum(c.get("output_tokens", 0) for c in inference_calls)})
    groups = {}
    for variant in ("A", "B", "C", "D"):
        subset = [r for r in rows if r["variant"] == variant and r["repeat"] == 0]
        scored = [r for r in subset if r["score"] is not None]
        latency = [r["latency_ms"] for r in subset if r["latency_ms"] is not None]
        groups[variant] = {"expected": len(subset), "statuses": dict(Counter(r["status"] for r in subset)),
            "scored": len(scored), "score_mean": mean(r["score"] for r in scored) if scored else None,
            "score_coverage": len(scored)/len(subset) if subset else 0,
            "axes": {axis: mean(r["axes"][axis] for r in scored) for axis in scored[0]["axes"]} if scored else {},
            "latency_p50_ms": percentile(latency, .5), "latency_p95_ms": percentile(latency, .95),
            "cost_per_question_cny": mean(r["cost_cny"] for r in subset) if subset else None}
    ready = any(r["score"] is not None for r in rows) and all(r["status"] != "missing" and
        (r["status"] != "completed" or r["score_status"] == "scored") for r in rows)
    phases = defaultdict(float)
    for call in calls:
        phases[call["phase"]] += call["cost_cny"]
    for variant, group in groups.items():
        per_question = group["cost_per_question_cny"]
        group["cost_per_question_with_seed_cny"] = (
            per_question + phases.get("seed", 0)/group["expected"] if per_question is not None and group["expected"]
            and variant != "A" else per_question)
    repeat_groups = defaultdict(list)
    for row in rows:
        if row["variant"] in ("B", "C") and row["score"] is not None:
            repeat_groups[(row["sample_id"], row["variant"])].append(row["score"])
    summary = {"protocol": manifest["protocol"], "ready_for_comparison": ready,
        "groups": groups, "cost_by_phase_cny": dict(phases),
        "total_charged_or_reserved_cny": sum(phases.values()),
        "comparisons": {"C-B": paired_bootstrap(rows, "C", "B"), "C-D": paired_bootstrap(rows, "C", "D")} if ready else {},
        "repeat_score_ranges": {f"{sid}_{variant}": max(scores)-min(scores)
                                for (sid, variant), scores in repeat_groups.items() if len(scores) == 3},
        "cost_note": "Token-priced estimates; missing usage retains conservative reservations. Reconcile with provider invoices."}
    output = run / "reports" / split
    write_json(output / "summary.json", summary)
    write_json(output / "rows.json", rows)
    if rows:
        with (output / "results.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# SWE-QA-Pro 固定子集 · 自定义 RepoReaper Agent", "",
             "正式对比已就绪。" if ready else "运行或评分尚未完整，以下为进度统计，不能用于效果结论。", "",
             "总分为 5–50 分，不是准确率；均分仅覆盖有评分答案，必须同时阅读分母。", "",
             "|组|题数|有评分|均分 /50|p50 ms|p95 ms|单题费用估算 CNY|", "|---|---:|---:|---:|---:|---:|---:|"]
    for v, g in groups.items():
        lines.append(f"|{v}|{g['expected']}|{g['scored']}|{g['score_mean']}|{g['latency_p50_ms']}|{g['latency_p95_ms']}|{g['cost_per_question_cny']}|")
    lines += ["", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```"]
    (output / "report.md").write_text("\n".join(lines)+"\n")
    # Standalone, dependency-free SVG suitable for exporting with the report.
    points = [(v, g) for v, g in groups.items() if g["score_mean"] is not None]
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440" viewBox="0 0 720 440">',
           '<rect width="720" height="440" fill="white"/><path d="M70 40V380H680" fill="none" stroke="black"/>',
           '<text x="240" y="425">Cost per question (CNY estimate)</text>',
           '<text x="15" y="25">QA score /50</text>']
    max_cost = max((g["cost_per_question_cny"] for _, g in points), default=1) or 1
    for v, g in points:
        x, y = 70+580*g["cost_per_question_cny"]/max_cost, 380-330*g["score_mean"]/50
        svg.append(f'<circle cx="{x}" cy="{y}" r="7" fill="#2166ac"/><text x="{x+10}" y="{y}">{v}: {g["score_mean"]:.2f}, CNY {g["cost_per_question_cny"]:.4f}</text>')
    for score_value in (0, 10, 20, 30, 40, 50):
        svg.append(f'<text x="35" y="{380-330*score_value/50}">{score_value}</text>')
    svg.append('</svg>')
    (output / "quality_cost.svg").write_text("".join(svg))
    export_review(run, schedule, output)
    summarize_evidence(run, schedule, output)
    return summary


def export_review(run, schedule, output):
    packets, key = [], {}
    for job in schedule:
        if not job["sample"]["evidence_review"] or job["variant"] not in ("B", "C") or job["repeat"]:
            continue
        path = run / "jobs" / job["job_id"] / "record.json"
        if not path.exists():
            continue
        record = read_json(path)
        from .data import digest
        blind_id = digest(["blind-review", job["job_id"]])[:16]
        key[blind_id] = job["job_id"]
        # No variant, score, or tool trace is exposed in the blind answer packet.
        packets.append({"blind_id": blind_id, "sample_id": job["sample"]["id"],
            "repo": job["sample"]["repo"], "commit_id": job["sample"]["commit_id"],
            "question": job["sample"]["question"], "answer": record["result"].get("answer", ""),
            "correctness": None, "supported": None, "failure_category": None, "notes": ""})
    # Preserve completed human annotations when refreshing the report.
    existing = read_json(output / "blind_review.json") if (output / "blind_review.json").exists() else []
    merged = {p["blind_id"]: p for p in packets}
    merged.update({p["blind_id"]: p for p in existing})
    write_json(output / "blind_review.json", sorted(merged.values(), key=lambda p: p["blind_id"]))
    write_json(output / "review_key.json", key)


def summarize_evidence(run, schedule, output):
    labels = read_json(run / "evidence_labels.json") if (run / "evidence_labels.json").exists() else {}
    details = []
    for job in schedule:
        label = labels.get(job["sample"]["id"], {})
        required = set(label.get("required_files", []))
        path = run / "jobs" / job["job_id"] / "record.json"
        if not required or not label.get("verified_before_review") or not path.exists() or job["repeat"]:
            continue
        result = read_json(path)["result"]
        initial = {e["file"] for e in result.get("evidence", []) if e["round"] == 0}
        final = {e["file"] for e in result.get("evidence", [])}
        new = {c["file"] for c in result.get("tool_calls", []) if c["success"]} - initial
        details.append({"job_id": job["job_id"], "variant": job["variant"],
            "initial_file_recall": len(initial & required)/len(required),
            "final_file_recall": len(final & required)/len(required),
            "jit_required_file_precision": len(new & required)/len(new) if new else None,
            "required_files": sorted(required), "initial_files": sorted(initial), "final_files": sorted(final)})
    reviews = read_json(output / "blind_review.json")
    counts = Counter(r["failure_category"] for r in reviews if r.get("failure_category") not in (None, "", "none"))
    write_json(output / "evidence_metrics.json", {"label_source": "self-annotated supplement, not official benchmark labels",
        "evaluated_records": len(details), "details": details, "human_failure_categories": dict(counts),
        "reviewed_answers": sum(r.get("correctness") is not None for r in reviews)})
    categories = sorted(counts)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="720" height="360"><rect width="720" height="360" fill="white"/>']
    if not categories:
        svg.append('<text x="30" y="50">Human review pending; no failure statistics available.</text>')
    for index, category in enumerate(categories):
        y = 30+45*index
        width = 380*counts[category]/max(counts.values())
        svg.append(f'<text x="10" y="{y+16}">{html.escape(category)}</text><rect x="200" y="{y}" width="{width}" height="24" fill="#b35806"/><text x="{210+width}" y="{y+16}">{counts[category]}</text>')
    (output / "failure_types.svg").write_text("".join(svg)+"</svg>")
