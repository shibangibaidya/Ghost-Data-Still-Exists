import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from conftest import DATASET_DIR
from deletion_audit.dataset import load_dataset
from deletion_audit.model import Case, CaseResult, Phase, Step, Verdict
from deletion_audit.submission import (
    Severity,
    check_submission,
    finalize_rows,
    parse_submission,
    render_submission,
)

T0 = datetime(2026, 9, 1, tzinfo=UTC)
AUDIT = datetime(2026, 9, 15, 12, tzinfo=UTC)
TIMES = {"evidence_01": datetime(2026, 9, 10, tzinfo=UTC), "evidence_late": datetime(2026, 9, 20, tzinfo=UTC)}


def case(case_id: str) -> Case:
    return Case(case_id, "a@b.com", T0, "COMPLETE", T0, AUDIT, "delete my account")


def finding(resource: str = "search_1", evidence: str = "evidence_01") -> Step:
    return Step(Phase.FINDING, "search_service", resource, "REPORT_REMAINING_RECORD", "PRESENT", evidence)


def remediation(resource: str = "search_1") -> Step:
    return Step(Phase.REMEDIATION, "search_service", resource, "DELETE_RECORD", "ABSENT", "")


def verification(resource: str = "search_1", evidence: str = "") -> Step:
    return Step(Phase.VERIFICATION, "search_service", resource, "VERIFY_RECORD_ABSENT", "ABSENT", evidence)


def errors(results, cases=None):
    found = check_submission(results, cases or [case(r.case_id) for r in results], TIMES.get)
    return [v for v in found if v.severity is Severity.ERROR]


def rules_hit(results) -> set[str]:
    return {violation.rule for violation in errors(results)}


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET_DIR)


def test_sample_output_round_trips_and_passes(dataset) -> None:
    text = (DATASET_DIR / "sample_output.csv").read_text(encoding="utf-8")
    results = parse_submission(text)

    violations = check_submission(results, dataset.sample_cases, dataset.citation_time)

    assert len(results) == 32
    assert [v for v in violations if v.severity is Severity.ERROR] == []
    assert parse_submission(render_submission(results)) == results


def test_rendering_format() -> None:
    result = CaseResult("case_1", Verdict.INCOMPLETE, (finding(), remediation(), verification()))

    text = render_submission([result])

    header, row = text.split("\n")[:2]
    assert header == "case_id,deletion_status,steps_json"
    assert text.endswith("\n") and "\r" not in text
    steps = json.loads(row.split(",", 2)[2].strip('"').replace('""', '"'))
    assert list(steps[0]) == [
        "step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id",
    ]
    assert [step["step_no"] for step in steps] == [1, 2, 3]


def test_rows_render_in_numeric_case_order() -> None:
    results = [CaseResult(f"case_{n}", Verdict.NOT_APPLICABLE, ()) for n in (100, 33, 99)]

    lines = render_submission(results).splitlines()[1:]

    assert [line.split(",")[0] for line in lines] == ["case_33", "case_99", "case_100"]


def test_missing_and_extra_cases_are_errors() -> None:
    results = [CaseResult("case_1", Verdict.NOT_APPLICABLE, ()), CaseResult("case_9", Verdict.NOT_APPLICABLE, ())]

    found = errors(results, cases=[case("case_1"), case("case_2")])

    assert {v.rule for v in found} == {"missing_case", "unexpected_case"}


@pytest.mark.parametrize(
    ("result", "rule"),
    [
        (CaseResult("c_1", Verdict.NOT_APPLICABLE, (verification(evidence="evidence_01"),)), "not_applicable_steps"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (replace(finding(), state="ABSENT"),)), "invalid_triple"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (replace(finding(), operation="DELETE_RECORD"),)), "invalid_triple"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (remediation(), finding())), "phase_order"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding("search_2"), finding("search_1"))), "sort_order"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding(evidence=""),)), "finding_evidence"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding(), replace(remediation(), evidence_id="evidence_01"))), "remediation_evidence"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding(), verification(evidence="evidence_01"))), "verification_evidence"),
        (CaseResult("c_1", Verdict.COMPLETE, (verification(evidence=""),)), "verification_evidence"),
        (CaseResult("c_1", Verdict.COMPLETE, (finding(), verification(evidence="evidence_01"))), "complete_phases"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding(evidence="evidence_late"),)), "citation_after_audit"),
        (CaseResult("c_1", Verdict.INCOMPLETE, (finding(evidence="evidence_nope"),)), "unknown_citation"),
    ],
)
def test_each_contract_rule_has_a_failing_case(result: CaseResult, rule: str) -> None:
    assert rule in rules_hit([result])


def test_valid_incomplete_and_complete_cases_pass() -> None:
    incomplete = CaseResult("c_1", Verdict.INCOMPLETE, (finding(), remediation(), verification()))
    complete = CaseResult("c_2", Verdict.COMPLETE, (verification(evidence="evidence_01"),))

    assert errors([incomplete, complete]) == []


def test_empty_verdicts_are_warnings_not_errors() -> None:
    results = [CaseResult("c_1", Verdict.INCOMPLETE, ()), CaseResult("c_2", Verdict.COMPLETE, ())]

    found = check_submission(results, [case("c_1"), case("c_2")], TIMES.get)

    assert {(v.severity, v.rule) for v in found} == {
        (Severity.WARNING, "incomplete_without_finding"),
        (Severity.WARNING, "complete_without_verification"),
    }


def test_finalize_replaces_only_violating_cases_and_fills_missing() -> None:
    good = CaseResult("c_1", Verdict.INCOMPLETE, (finding(), remediation(), verification()))
    bad = CaseResult("c_2", Verdict.INCOMPLETE, (finding(evidence=""),))
    cases = [case("c_1"), case("c_2"), case("c_3")]

    final = finalize_rows([good, bad], cases, TIMES.get)

    assert [r.case_id for r in final.results] == ["c_1", "c_2", "c_3"]
    assert final.results[0] == good
    assert final.results[1] == CaseResult("c_2", Verdict.INCOMPLETE, ())
    assert final.replaced == ("c_2", "c_3")
    assert [v for v in check_submission(final.results, cases, TIMES.get) if v.severity is Severity.ERROR] == []
