"""Deletion audit CLI: writes output.csv with one row per request.

Run from the repository root:  python code/main.py
"""

import sys

# Set before importing the package so no __pycache__ lands inside code/ (it ships in code.zip).
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

from deletion_audit.audit import VERIFICATION_PATH, run_audit  # noqa: E402
from deletion_audit.dataset import DatasetError  # noqa: E402
from deletion_audit.llm import CacheMissError  # noqa: E402
from deletion_audit.runtime import make_client  # noqa: E402
from deletion_audit.settings import Settings  # noqa: E402
from deletion_audit.submission import Severity, finalize_rows, render_submission  # noqa: E402
from deletion_audit.tracing import write_traces  # noqa: E402
from deletion_audit.usage import render_usage_report, summarize_usage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "dataset"
OFFICIAL_REQUESTS = DATASET_DIR / "deletion_requests.csv"
USAGE_REPORT = REPO_ROOT / "code" / "evaluation" / "usage_report.md"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, default=OFFICIAL_REQUESTS)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--trace-dir", type=Path, default=REPO_ROOT / ".audit_traces")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="model-answer cache (default: code/cache/llm)")
    parser.add_argument("--verification", type=Path, default=VERIFICATION_PATH,
                        help="corrections overlay for visual extractions (default: the shipped one)")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="development only: use visual extractions not yet verified by a person")
    parser.add_argument("--offline", action="store_true",
                        help="replay cached model answers only; never call the provider")
    parser.add_argument("--usage-report", type=Path, default=None,
                        help="where to write the usage report (default: code/evaluation/usage_report.md, "
                             "official requests only)")
    return parser.parse_args(argv)


def write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    settings = Settings.from_environment(REPO_ROOT)
    llm = make_client(settings, offline=args.offline, cache_dir=args.cache_dir)
    try:
        run = run_audit(DATASET_DIR, args.requests, llm, intent_model=settings.intent_model or "",
                        visual_model=settings.visual_model or "",
                        require_verified=not args.allow_unverified,
                        verification_path=args.verification)
    except CacheMissError as error:
        print(f"{error}. Configure the provider in .env, or run without --offline.", file=sys.stderr)
        return 2
    except DatasetError as error:
        print(f"Dataset problem, nothing written: {error}", file=sys.stderr)
        return 2
    final = finalize_rows(run.results, run.cases, run.dataset.citation_time)
    submission = render_submission(final.results)
    write_atomically(args.output, submission)
    write_traces(args.trace_dir, run.traces)

    verdicts = Counter(result.verdict.value for result in final.results)
    usage_path = args.usage_report
    if usage_path is None and args.requests.resolve() == OFFICIAL_REQUESTS.resolve():
        usage_path = USAGE_REPORT
    if usage_path is not None:
        summary = summarize_usage(llm.usage_events, len(final.results))
        meta = [
            ("Command", "`python code/main.py" + (" --offline`" if args.offline else "`")),
            ("Requests", f"`{args.requests.name}`, {len(final.results)} rows"),
            ("Verdicts", ", ".join(f"{name} {count}" for name, count in sorted(verdicts.items()))),
            ("Mode", "offline (cache replay only)" if llm.is_offline
             else "live calls allowed for answers not in the cache"),
            ("Configured models", f"intent `{settings.intent_model}`, visual `{settings.visual_model}`"),
            ("output.csv sha256", f"`{hashlib.sha256(submission.encode('utf-8')).hexdigest()}`"),
        ]
        write_atomically(usage_path, render_usage_report(summary, meta))
    warnings = Counter(v.rule for v in final.violations if v.severity is Severity.WARNING)
    print(f"Wrote {len(final.results)} rows to {args.output}")
    print("Verdicts: " + ", ".join(f"{name}={count}" for name, count in sorted(verdicts.items())))
    if warnings:
        print("Warnings: " + ", ".join(f"{rule}={count}" for rule, count in sorted(warnings.items())))
    if usage_path is not None:
        overall = summary.overall
        print(f"Model calls: {overall.calls} ({overall.live_calls} live, {overall.replayed_calls} replayed); "
              f"usage report: {usage_path}")
    for case_id, error in run.failures:
        print(f"FAILED {case_id}: {error}", file=sys.stderr)
    if final.replaced:
        print(f"Fallback rows for {len(final.replaced)} cases: {', '.join(final.replaced)}",
              file=sys.stderr)
    return 1 if run.failures or final.replaced else 0


if __name__ == "__main__":
    sys.exit(main())
