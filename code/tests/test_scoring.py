import pytest

from deletion_audit.model import CaseResult, Link, LinkBasis, Phase, ResourceKey, Step, Verdict
from evaluation.scoring import score_cases, score_links


def step(phase: Phase, resource: str, operation: str, state: str, evidence: str = "") -> Step:
    return Step(phase, "search_service", resource, operation, state, evidence)


F = step(Phase.FINDING, "s1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01")
R = step(Phase.REMEDIATION, "s1", "DELETE_RECORD", "ABSENT")
V = step(Phase.VERIFICATION, "s1", "VERIFY_RECORD_ABSENT", "ABSENT")
V2 = step(Phase.VERIFICATION, "s2", "VERIFY_RECORD_ABSENT", "ABSENT")


def test_perfect_prediction_scores_one() -> None:
    gold = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V))}

    metrics = score_cases(gold, gold)

    assert metrics.cases == 1
    assert metrics.status_accuracy == 1.0
    assert metrics.step_f1 == 1.0
    assert metrics.sequence_exact == 1.0


def test_partial_prediction_is_scored_independently() -> None:
    gold = {
        "c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V, V2)),
        "c2": CaseResult("c2", Verdict.COMPLETE, (step(Phase.VERIFICATION, "s9", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_02"),)),
    }
    predicted = {
        # wrong citation on the finding, one verification missing
        "c1": CaseResult("c1", Verdict.INCOMPLETE, (step(Phase.FINDING, "s1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_09"), R, V)),
        "c2": CaseResult("c2", Verdict.INCOMPLETE, ()),
    }

    metrics = score_cases(predicted, gold)

    assert metrics.status_accuracy == 0.5
    # tuples: predicted 3, gold 5, matching exactly 2 (R, V)
    assert metrics.step_precision == pytest.approx(2 / 3)
    assert metrics.step_recall == pytest.approx(2 / 5)
    # without evidence_id the finding matches too: 3 of 3 predicted, 3 of 5 gold
    assert metrics.key_precision == pytest.approx(1.0)
    assert metrics.key_recall == pytest.approx(3 / 5)
    # aligned pairs: F, R, V -> evidence_id agrees on 2 of 3, state on 3 of 3
    assert metrics.field_accuracy["evidence_id"] == pytest.approx(2 / 3)
    assert metrics.field_accuracy["state"] == pytest.approx(1.0)
    assert metrics.sequence_exact == 0.0


def test_missing_prediction_counts_as_empty() -> None:
    gold = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V))}

    metrics = score_cases({}, gold)

    assert metrics.status_accuracy == 0.0
    assert metrics.step_recall == 0.0
    assert metrics.step_precision == 0.0


def test_link_metrics_per_basis() -> None:
    gold = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V, V2))}
    links = {
        "c1": (
            Link(ResourceKey("search_service", "s1"), LinkBasis.IDENTIFIER, ("evidence_01",)),
            Link(ResourceKey("search_service", "s7"), LinkBasis.BURST, ("activity_9",)),
        )
    }

    metrics = score_links(links, gold)

    assert metrics.precision == pytest.approx(1 / 2)
    assert metrics.recall == pytest.approx(1 / 2)
    assert metrics.by_basis[LinkBasis.IDENTIFIER] == (1, 1)
    assert metrics.by_basis[LinkBasis.BURST] == (1, 0)
    assert metrics.missed == {"c1": ("search_service/s2",)}


def test_row_accuracy_counts_step_rows_and_fully_correct_output_rows() -> None:
    gold = {
        "c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V, V2)),
        "c2": CaseResult("c2", Verdict.INCOMPLETE, (F, R, V)),
        "c3": CaseResult("c3", Verdict.NOT_APPLICABLE, ()),
    }
    predicted = {
        "c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V)),  # one step row missing
        "c2": CaseResult("c2", Verdict.INCOMPLETE, (F, R, V)),  # fully correct
        "c3": CaseResult("c3", Verdict.INCOMPLETE, ()),  # same (empty) steps, wrong status
    }

    metrics = score_cases(predicted, gold)

    # step rows: 3 of 4 matched in c1, 3 of 3 in c2, none expected in c3 -> 6 of 7
    assert metrics.step_row_accuracy == pytest.approx(6 / 7)
    # output rows: only c2 has both the status and the whole steps_json right
    assert metrics.output_row_accuracy == pytest.approx(1 / 3)
    assert metrics.sequence_exact == pytest.approx(2 / 3)  # c3's steps match although its row is wrong


def test_a_step_row_with_a_wrong_field_does_not_count_as_matched() -> None:
    gold = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (F, R, V))}
    wrong_evidence = step(Phase.FINDING, "s1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_02")
    predicted = {"c1": CaseResult("c1", Verdict.INCOMPLETE, (wrong_evidence, R, V))}

    metrics = score_cases(predicted, gold)

    assert metrics.step_row_accuracy == pytest.approx(2 / 3)
    assert metrics.output_row_accuracy == 0.0
