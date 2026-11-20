"""python -m evaluation.benchmark --help"""
import argparse
import asyncio
import json
import shutil
from pathlib import Path

from .data import download_dataset, prepare, read_json


def main():
    parser = argparse.ArgumentParser(description="SWE-QA-Pro fixed subset / RepoReaper custom Agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="Read credential availability only; no paid requests")
    fetch = commands.add_parser("fetch", help="Download and pin public benchmark and upstream rubric")
    fetch.add_argument("--output", required=True)
    prep = commands.add_parser("prepare", help="Create reference-separated immutable sample manifests")
    prep.add_argument("--data", required=True)
    prep.add_argument("--run", required=True)
    prep.add_argument("--per-repo", type=int, choices=[2, 4], default=4)
    prep_jit = commands.add_parser("prepare-jit", help="Freeze unused questions against earlier sealed seeds")
    prep_jit.add_argument("--data", required=True)
    prep_jit.add_argument("--run", required=True)
    prep_jit.add_argument("--seed-run", required=True)
    prep_jit.add_argument("--repo", action="append", required=True)
    prep_jit.add_argument("--experiment", choices=["jev", "exploration", "source", "tools"], default="jev")
    prep_jit.add_argument("--exclude-run", action="append", default=[])
    clone = commands.add_parser("clone", help="Fetch repository objects at exact benchmark commits")
    clone.add_argument("--run", required=True)
    clone.add_argument("--repos", required=True)
    run = commands.add_parser("run", help="Paid, metered, isolated generation; completed jobs are never repeated")
    run.add_argument("--run", required=True)
    run.add_argument("--repos", required=True)
    run.add_argument("--rates", required=True)
    run.add_argument("--split", choices=["dev", "test"], default="dev")
    run.add_argument("--repeats", action="store_true")
    run.add_argument("--budget", type=float, default=450)
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--model", help="Explicit offline-only model override, frozen for all groups")
    run.add_argument("--seed-run", help="Sealed legacy indexes for the JIT comparison")
    run.add_argument("--jev-timeout", type=float, default=0.45)
    run.add_argument("--jev-model", default="jev-latest")
    run_e = commands.add_parser("run-e", help="Run resumable full-index one-shot E baseline only")
    run_e.add_argument("--run", required=True)
    run_e.add_argument("--source-run", required=True)
    run_e.add_argument("--repos", required=True)
    run_e.add_argument("--rates", required=True)
    run_e.add_argument("--budget", type=float, default=15)
    run_e.add_argument("--timeout", type=int, default=600)
    run_e.add_argument("--seed-timeout", type=int, default=21600)
    run_e.add_argument("--model", required=True)
    judge = commands.add_parser("judge", help="Independent official-rubric scoring, with explicit failures")
    judge.add_argument("--run", required=True)
    judge.add_argument("--split", choices=["dev", "test"], default="dev")
    judge.add_argument("--retry-failed", action="store_true")
    select = commands.add_parser("select", help="Choose 104 or 52 formal samples from pilot costs")
    select.add_argument("--run", required=True)
    report = commands.add_parser("report", help="Coverage, paired bootstrap, CSV/SVG and blind review packets")
    report.add_argument("--run", required=True)
    report.add_argument("--split", choices=["dev", "test"], default="test")
    report_e = commands.add_parser("report-e", help="Compare prior C with the new E baseline")
    report_e.add_argument("--run", required=True)
    report_e.add_argument("--source-run", required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        from .runner import preflight
        result = preflight()
    elif args.command == "fetch":
        result = download_dataset(args.output)
    elif args.command == "prepare":
        data = Path(args.data)
        result = prepare(read_json(data / "dataset.json"), read_json(data / "provenance.json"),
                         args.run, args.per_repo)
        shutil.copyfile(data / "judge_prompt.txt", Path(args.run) / "judge_prompt.txt")
        result = {"manifest_hash": result["manifest_hash"], "samples": len(result["samples"])}
    elif args.command == "prepare-jit":
        from .data import load_manifest, prepare_jit
        data = Path(args.data)
        result = prepare_jit(read_json(data / "dataset.json"), read_json(data / "provenance.json"),
                             load_manifest(Path(args.seed_run) / "manifest.json"), args.run, args.repo,
                             experiment=args.experiment,
                             excluded_manifests=tuple(load_manifest(Path(run) / "manifest.json")
                                                      for run in args.exclude_run))
        shutil.copyfile(data / "judge_prompt.txt", Path(args.run) / "judge_prompt.txt")
        result = {"manifest_hash": result["manifest_hash"], "samples": len(result["samples"])}
    elif args.command == "clone":
        from .runner import clone_repos
        clone_repos(read_json(Path(args.run) / "manifest.json"), args.repos)
        result = {"repositories_ready": True}
    elif args.command == "run":
        from .runner import run_jobs
        if not 0 < args.budget <= 450:
            parser.error("Budget must be positive and at most 450 CNY (50 CNY reserve remains untouched)")
        if args.repeats and args.split != "test":
            parser.error("Repeats apply only to formal test samples")
        result = run_jobs(args.run, args.repos, read_json(args.rates), args.split,
                          args.repeats, args.budget, args.timeout, args.model, seed_run=args.seed_run,
                          jev_timeout=args.jev_timeout, jev_model=args.jev_model)
    elif args.command == "run-e":
        from .full_static import run_full_static
        result = run_full_static(args.run, args.source_run, args.repos, read_json(args.rates),
                                 args.budget, args.timeout, args.seed_timeout, args.model)
    elif args.command == "judge":
        from .judge import score
        result = asyncio.run(score(args.run, args.split, args.retry_failed))
    elif args.command == "select":
        from .judge import select_formal
        result = select_formal(args.run)
    elif args.command == "report-e":
        from .full_static import comparison_report
        result = comparison_report(args.run, args.source_run)
    else:
        from .report import generate_report
        result = generate_report(args.run, args.split)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
