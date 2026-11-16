import ast
import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import DATASET_DIR
from evaluation.verify_output import check_output, load_audit_times, load_citation_times, main

CHECKER = Path(__file__).resolve().parents[1] / "evaluation" / "verify_output.py"
AUDIT = {"case_1": datetime(2026, 9, 15, 12, tzinfo=UTC), "case_2": datetime(2026, 9, 15, 12, tzinfo=UTC)}
CITED = {"evidence_01": datetime(2026, 9, 15, 11, tzinfo=UTC), "evidence_99": datetime(2026, 9, 16, tzinfo=UTC),
         "activity_7": datetime(2026, 9, 1, tzinfo=UTC)}


def step(no, phase, operation, state, evidence="", resource="account_1", system="account_service"):
    return {"step_no": no, "phase": phase, "system_id": system, "resource_id": resource,
            "operation": operation, "state": state, "evidence_id": evidence}


INCOMPLETE = [
    step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT"),
    step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
]
COMPLETE = [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "activity_7")]


def renumber(steps: list[dict]) -> list[dict]:
    for no, item in enumerate(steps, start=1):
        item["step_no"] = no
    return steps


def render(*rows) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["case_id", "deletion_status", "steps_json"])
    for case_id, status, steps in rows:
        writer.writerow([case_id, status, steps if isinstance(steps, str) else json.dumps(steps)])
    return out.getvalue()


def errors(text: str) -> list[str]:
    return [issue.message for issue in check_output(text, AUDIT, CITED) if issue.level == "ERROR"]


def test_a_valid_file_has_no_issues() -> None:
    assert check_output(render(("case_1", "INCOMPLETE", INCOMPLETE), ("case_2", "COMPLETE", COMPLETE)),
                        AUDIT, CITED) == []


def test_the_public_sample_answers_pass() -> None:
    issues = check_output((DATASET_DIR / "sample_output.csv").read_text(encoding="utf-8"),
                          load_audit_times(DATASET_DIR / "sample_deletion_requests.csv"),
                          load_citation_times(DATASET_DIR))

    assert issues == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda s: s[0].update(evidence_id="evidence_404"), "does not exist"),
    (lambda s: s[0].update(evidence_id="evidence_99"), "after audit_at"),
    (lambda s: s[0].update(evidence_id=""), "must cite evidence"),
    (lambda s: s[1].update(evidence_id="evidence_01"), "must have an empty evidence_id"),
    (lambda s: s[0].update(state="ACTIVE"), "not an allowed triple"),
    (lambda s: s[1].update(operation="PURGE_RECORD"), "not an allowed triple"),
    (lambda s: s[2].update(phase="CHECK"), "unknown phase"),
    (lambda s: s[2].update(step_no=4), "step_no"),
    (lambda s: s[2].update(step_no=True), "step_no"),
    (lambda s: s[2].update(resource_id=""), "empty system_id or resource_id"),
    (lambda s: s[2].pop("evidence_id"), "has fields"),
    (lambda s: s[2].update(extra="x"), "has fields"),
    (lambda s: renumber(s.reverse() or s), "phases are not in"),
    (lambda s: s[2].update(operation="VERIFY_RECORD_REDACTED", state="REDACTED"), "has no verification"),
])
def test_each_step_rule_is_enforced(mutate, expected: str) -> None:
    steps = json.loads(json.dumps(INCOMPLETE))
    mutate(steps)

    found = errors(render(("case_1", "INCOMPLETE", steps), ("case_2", "COMPLETE", COMPLETE)))

    assert any(expected in message for message in found), found


@pytest.mark.parametrize("rows, expected", [
    ((("case_2", "COMPLETE", COMPLETE), ("case_1", "INCOMPLETE", INCOMPLETE)), "not sorted"),
    ((("case_1", "INCOMPLETE", INCOMPLETE),), "without a row"),
    ((("case_1", "INCOMPLETE", INCOMPLETE), ("case_1", "INCOMPLETE", INCOMPLETE),
      ("case_2", "COMPLETE", COMPLETE)), "duplicate row"),
    ((("case_1", "INCOMPLETE", INCOMPLETE), ("case_2", "COMPLETE", COMPLETE),
      ("case_3", "COMPLETE", COMPLETE)), "not in the request file"),
    ((("case_1", "DONE", INCOMPLETE), ("case_2", "COMPLETE", COMPLETE)), "is not allowed"),
    ((("case_1", "NOT_APPLICABLE", INCOMPLETE), ("case_2", "COMPLETE", COMPLETE)), "must have steps_json []"),
    ((("case_1", "INCOMPLETE", "[{"), ("case_2", "COMPLETE", COMPLETE)), "not valid JSON"),
    ((("case_1", "INCOMPLETE", "{}"), ("case_2", "COMPLETE", COMPLETE)), "not a JSON array"),
    ((("case_1", "COMPLETE", INCOMPLETE), ("case_2", "COMPLETE", COMPLETE)), "VERIFICATION steps only"),
])
def test_each_file_and_row_rule_is_enforced(rows, expected: str) -> None:
    found = errors(render(*rows))

    assert any(expected in message for message in found), found


def test_a_record_changed_before_its_flow_is_disabled_is_an_error() -> None:
    steps = [
        step(1, "FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_01", "data_flow_1", "integration_service"),
        step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT"),
        step(3, "REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED", "", "data_flow_1", "integration_service"),
        step(4, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
        step(5, "VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "", "data_flow_1", "integration_service"),
    ]

    found = errors(render(("case_1", "INCOMPLETE", steps), ("case_2", "COMPLETE", COMPLETE)))

    assert any("before every data flow is disabled" in message for message in found)


def test_wrong_header_stops_the_check() -> None:
    assert errors("case_id,status,steps_json\ncase_1,COMPLETE,[]\n") == [
        "header is ['case_id', 'status', 'steps_json'], expected ['case_id', 'deletion_status', 'steps_json']"
    ]


def test_suspicious_but_legal_shapes_are_warnings() -> None:
    no_finding = renumber(json.loads(json.dumps(INCOMPLETE[1:])))

    issues = check_output(render(("case_1", "INCOMPLETE", no_finding), ("case_2", "COMPLETE", [])), AUDIT, CITED)

    assert sorted((i.level, i.case_id) for i in issues) == [("WARNING", "case_1"), ("WARNING", "case_2")]


def test_cli_exit_code_reflects_errors(workdir: Path) -> None:
    requests = workdir / "requests.csv"
    requests.write_text("case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
                        "case_1,a@b.co,2026-09-01T00:00:00Z,COMPLETE,2026-09-02T00:00:00Z,2026-09-15T12:00:00Z,x\n",
                        encoding="utf-8")
    good, bad = workdir / "good.csv", workdir / "bad.csv"
    good.write_text(render(("case_1", "NOT_APPLICABLE", [])), encoding="utf-8")
    bad.write_text(render(("case_1", "NOT_APPLICABLE", COMPLETE)), encoding="utf-8")

    assert main(["--output", str(good), "--requests", str(requests), "--dataset", str(DATASET_DIR)]) == 0
    assert main(["--output", str(bad), "--requests", str(requests), "--dataset", str(DATASET_DIR)]) == 1


def test_the_checker_shares_no_code_with_the_pipeline() -> None:
    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert not any(name.split(".")[0] in ("deletion_audit", "evaluation") for name in imported)
