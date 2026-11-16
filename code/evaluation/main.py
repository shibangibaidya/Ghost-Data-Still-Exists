"""Evaluates the audit on the sample requests (against dataset/sample_output.csv) and on the
custom cases (against code/evaluation/custom_cases/expected.csv), then rewrites the generated
block of code/evaluation/evaluation_report.md.

Run from the repository root:  python code/evaluation/main.py [--offline] [--no-report]
"""

import sys

sys.dont_write_bytecode = True
# Run as a script, so make the `code/` folder importable (it holds deletion_audit and evaluation).
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from pathlib import Path  # noqa: E402

from deletion_audit.audit import AuditRun, run_audit  # noqa: E402
from deletion_audit.llm import CacheMissError, LlmClient  # noqa: E402
from deletion_audit.runtime import make_client  # noqa: E402
from deletion_audit.settings import Settings  # noqa: E402
from deletion_audit.submission import finalize_rows, parse_submission  # noqa: E402
from evaluation.report import render_block, replace_generated  # noqa: E402
from evaluation.scoring import CaseMetrics, LinkMetrics, score_cases, score_links  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
EVALUATION_DIR = Path(__file__).resolve().parent
CUSTOM_REQUESTS = EVALUATION_DIR / "custom_cases" / "requests.csv"
CUSTOM_EXPECTED = EVALUATION_DIR / "custom_cases" / "expected.csv"
REPORT_PATH = EVALUATION_DIR / "evaluation_report.md"


def format_case_metrics(metrics: CaseMetrics) -> list[str]:
    lines = [
        f"cases: {metrics.cases}",
        f"status accuracy: {metrics.status_accuracy:.3f}",
        f"row accuracy: step rows={metrics.step_row_accuracy:.3f} output rows={metrics.output_row_accuracy:.3f}",
        f"step tuples (with evidence_id): P={metrics.step_precision:.3f} "
        f"R={metrics.step_recall:.3f} F1={metrics.step_f1:.3f}",
        f"step tuples (without evidence_id): P={metrics.key_precision:.3f} "
        f"R={metrics.key_recall:.3f} F1={metrics.key_f1:.3f}",
        "aligned field accuracy: "
        + ", ".join(f"{name}={value:.3f}" for name, value in metrics.field_accuracy.items()),
        "positional field accuracy: "
        + ", ".join(f"{name}={value:.3f}" for name, value in metrics.positional_accuracy.items()),
        f"exact sequence match: {metrics.sequence_exact:.3f}",
        "status confusion (expected -> predicted): "
        + ", ".join(f"{a}->{b}={n}" for (a, b), n in sorted(metrics.status_confusion.items())),
    ]
    return lines


def format_link_metrics(metrics: LinkMetrics) -> list[str]:
    lines = [f"links: P={metrics.precision:.3f} R={metrics.recall:.3f}"]
    lines += [f"  {basis}: made={made} in_answer={hit}" for basis, (made, hit) in sorted(metrics.by_basis.items())]
    lines += [f"  missed {case_id}: {', '.join(items)}" for case_id, items in metrics.missed.items()]
    lines += [f"  extra {case_id}: {', '.join(items)}" for case_id, items in metrics.extra.items()]
    return lines


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    settings = Settings.from_environment(REPO_ROOT)
    llm = make_client(settings, offline="--offline" in sys.argv)
    try:
        samples, sample_links = _audit(llm, settings, DATASET_DIR / "sample_deletion_requests.csv")
        custom, _ = _audit(llm, settings, CUSTOM_REQUESTS)
    except CacheMissError as error:
        print(f"{error}. Run code/tools/warm_cache.py intent with the provider configured.", file=sys.stderr)
        return 2
    sample_gold = _answers(DATASET_DIR / "sample_output.csv")
    custom_gold = _answers(CUSTOM_EXPECTED)
    sample_metrics, links = score_cases(samples, sample_gold), score_links(sample_links, sample_gold)
    custom_metrics = score_cases(custom, custom_gold)

    print("\n".join(format_case_metrics(sample_metrics)))
    print("\n".join(format_link_metrics(links)))
    print("custom cases:")
    print("\n".join(format_case_metrics(custom_metrics)))
    for case_id, expected in sorted(custom_gold.items()):
        if custom.get(case_id) != expected:
            print(f"  custom mismatch {case_id}")
    if "--no-report" not in sys.argv:
        block = render_block(sample_metrics, links, custom_metrics, custom, custom_gold)
        REPORT_PATH.write_text(replace_generated(REPORT_PATH.read_text(encoding="utf-8"), block),
                               encoding="utf-8", newline="\n")
        print(f"updated {REPORT_PATH.relative_to(REPO_ROOT)}")
    return 0


def _audit(llm: LlmClient, settings: Settings, requests: Path) -> tuple[dict, dict]:
    """Final rows by case_id, and the run's links for link scoring."""
    run: AuditRun = run_audit(DATASET_DIR, requests, llm, intent_model=settings.intent_model or "",
                              visual_model=settings.visual_model or "",
                              require_verified="--allow-unverified" not in sys.argv)
    final = finalize_rows(run.results, run.cases, run.dataset.citation_time)
    if final.replaced:
        print(f"fallback rows ({requests.name}): {', '.join(final.replaced)}")
    return {r.case_id: r for r in final.results}, run.links


def _answers(path: Path) -> dict:
    return {r.case_id: r for r in parse_submission(path.read_text(encoding="utf-8"))}


if __name__ == "__main__":
    sys.exit(main())
