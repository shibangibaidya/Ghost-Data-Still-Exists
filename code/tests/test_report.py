from pathlib import Path

import pytest

from deletion_audit.dataset import read_cases
from deletion_audit.model import CaseResult, Phase, Step, Verdict
from deletion_audit.submission import parse_submission
from evaluation.report import BEGIN, END, _metric_table, replace_generated
from evaluation.scoring import score_cases

CUSTOM = Path(__file__).resolve().parents[1] / "evaluation" / "custom_cases"


def test_only_the_generated_block_is_rewritten() -> None:
    report = f"# Report\nhand-written\n{BEGIN}\nold numbers\n{END}\nfailures\n"

    updated = replace_generated(report, "new numbers")

    assert updated == f"# Report\nhand-written\n{BEGIN}\nnew numbers\n{END}\nfailures\n"
    assert replace_generated(updated, "new numbers") == updated


def test_a_report_without_markers_is_refused() -> None:
    with pytest.raises(ValueError, match="markers"):
        replace_generated("# Report\n", "numbers")


def test_custom_cases_have_one_expected_answer_each_and_never_official_ids() -> None:
    requests = read_cases(CUSTOM / "requests.csv")
    expected = parse_submission((CUSTOM / "expected.csv").read_text(encoding="utf-8"))

    assert len(requests) >= 3
    assert [c.case_id for c in requests] == [r.case_id for r in expected]
    assert all(c.case_id.startswith("custom_") for c in requests)


def test_the_metric_table_names_case_row_field_and_sequence_accuracy() -> None:
    finding = Step(Phase.FINDING, "search_service", "s1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01")
    gold = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (finding,))}

    table = "\n".join(_metric_table(score_cases(gold, gold)))

    for name in ("Case-level deletion-status accuracy",
                 "Row accuracy, step rows (matched / expected step rows)",
                 "Row accuracy, output rows (status and steps_json fully correct)",
                 "Field accuracy, aligned steps: evidence_id", "Field accuracy, by position: resource_id",
                 "Sequence accuracy (exact step list)"):
        assert f"| {name} | 1.000 |" in table
