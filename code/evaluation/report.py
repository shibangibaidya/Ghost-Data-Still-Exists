"""The generated metrics block of evaluation_report.md. Only the text between the markers is
rewritten; the hand-written sections around it are left as they are."""

from __future__ import annotations

from collections.abc import Mapping

from deletion_audit.model import CaseResult
from evaluation.scoring import CaseMetrics, LinkMetrics

BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"


def replace_generated(report: str, block: str) -> str:
    start, end = report.find(BEGIN), report.find(END)
    if start < 0 or end < start:
        raise ValueError(f"the report needs the markers {BEGIN} and {END}, in that order")
    return report[: start + len(BEGIN)] + "\n" + block.strip() + "\n" + report[end:]


def render_block(
    samples: CaseMetrics, links: LinkMetrics, custom: CaseMetrics,
    custom_predicted: Mapping[str, CaseResult], custom_expected: Mapping[str, CaseResult],
) -> str:
    lines = [
        "### Sample requests (32 cases, `dataset/sample_output.csv`)",
        "",
        *_metric_table(samples),
        "",
        "Status confusion (expected -> predicted): "
        + ", ".join(f"{a} -> {b}: {n}" for (a, b), n in sorted(samples.status_confusion.items())),
        "",
        f"Resource links: precision {links.precision:.3f}, recall {links.recall:.3f}.",
        "",
        "| Link basis | Links made | In the answer |",
        "|---|---|---|",
        *[f"| {basis} | {made} | {hit} |" for basis, (made, hit) in sorted(links.by_basis.items())],
        "",
        f"### Custom cases ({custom.cases} cases, `code/evaluation/custom_cases/`)",
        "",
        *_metric_table(custom),
        "",
        "| Case | Expected | Predicted | Steps expected | Steps predicted | Exact match |",
        "|---|---|---|---|---|---|",
    ]
    for case_id, expected in sorted(custom_expected.items()):
        predicted = custom_predicted.get(case_id)
        status = predicted.verdict.value if predicted else "not run"
        count = len(predicted.steps) if predicted else 0
        exact = "yes" if predicted == expected else "no"
        lines.append(f"| {case_id} | {expected.verdict.value} | {status} | {len(expected.steps)} | {count} | {exact} |")
    return "\n".join(lines)


def _metric_table(metrics: CaseMetrics) -> list[str]:
    rows = [
        ("Case-level deletion-status accuracy", metrics.status_accuracy),
        ("Row accuracy, step rows (matched / expected step rows)", metrics.step_row_accuracy),
        ("Row accuracy, output rows (status and steps_json fully correct)", metrics.output_row_accuracy),
        ("Step precision (full step)", metrics.step_precision),
        ("Step recall (full step)", metrics.step_recall),
        ("Step F1 (full step)", metrics.step_f1),
        ("Step F1 ignoring evidence_id", metrics.key_f1),
        *[(f"Field accuracy, aligned steps: {name}", value) for name, value in metrics.field_accuracy.items()],
        *[(f"Field accuracy, by position: {name}", value) for name, value in metrics.positional_accuracy.items()],
        ("Sequence accuracy (exact step list)", metrics.sequence_exact),
    ]
    return ["| Metric | Value |", "|---|---|", *[f"| {name} | {value:.3f} |" for name, value in rows]]
