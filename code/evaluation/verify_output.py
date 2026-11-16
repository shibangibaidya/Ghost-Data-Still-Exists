"""Independent checker for a submission file such as output.csv.

It re-states the output contract (AGENTS.md §6.3-6.6) from the challenge text and reads the
participant-visible dataset itself. It imports nothing from `deletion_audit`, so a mistake in the
pipeline's own contract module (`deletion_audit/submission.py`) cannot hide itself here.

Checks, per file: the exact header, one row per request, no unknown or repeated case_id, rows in
numeric case_id order. Per row: an allowed deletion_status, steps_json a JSON array of objects
with exactly the seven step fields, step_no 1..n, phases in FINDING, REMEDIATION, VERIFICATION
order, only allowed (phase, operation, state) triples, NOT_APPLICABLE with [], COMPLETE with
cited verifications only, cited findings and uncited remediation and verification in INCOMPLETE,
every evidence_id an existing file_id, activity_id or audit_id, and no citation of an item
captured (or logged) after the case's audit_at. Contract ordering inside remediation and
verification coverage of remediated resources are checked too.

Run from the repository root (standard library only):

    python code/evaluation/verify_output.py
    python code/evaluation/verify_output.py --output path/to/output.csv --requests dataset/sample_deletion_requests.csv

Exit code 0 when there is no ERROR, 1 when there is at least one. WARNINGs never fail the check.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

HEADER = ["case_id", "deletion_status", "steps_json"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
# AGENTS.md §6.4, copied from the challenge text: operation -> (phase, state).
OPERATIONS = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
# §6.5: flows are disabled and restores blocked before dependent records are changed.
GUARD_OPERATIONS = {"DISABLE_DATA_FLOW", "BLOCK_RESTORE"}
_NUMBER = re.compile(r"(\d+)$")


@dataclass(frozen=True, slots=True)
class Issue:
    level: str  # "ERROR" or "WARNING"
    case_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.level} {self.case_id}: {self.message}"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def load_audit_times(requests_path: Path) -> dict[str, datetime]:
    """audit_at per case_id, from the request file the output answers."""
    with requests_path.open(encoding="utf-8", newline="") as handle:
        return {row["case_id"]: parse_time(row["audit_at"]) for row in csv.DictReader(handle)}


def load_citation_times(dataset_dir: Path) -> dict[str, datetime]:
    """When each citable item became available: a file's manifest capture time, an activity's
    event time and an identity audit entry's event time."""
    sources = (
        ("evidence_files.csv", "file_id", "captured_at"),
        ("system_activity.csv", "activity_id", "event_time"),
        ("user_audit_log.csv", "audit_id", "event_time"),
    )
    times: dict[str, datetime] = {}
    for name, id_column, time_column in sources:
        with (dataset_dir / name).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                times[row[id_column]] = parse_time(row[time_column])
    return times


def check_output(text: str, audit_times: dict[str, datetime], citation_times: dict[str, datetime]) -> list[Issue]:
    issues: list[Issue] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header != HEADER:
        return [Issue("ERROR", "-", f"header is {header}, expected {HEADER}")]

    seen: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        if len(row) != 3:
            issues.append(Issue("ERROR", "-", f"line {line_no} has {len(row)} fields, expected 3"))
            continue
        case_id, status, steps_json = row
        if case_id in seen:
            issues.append(Issue("ERROR", case_id, "duplicate row"))
            continue
        seen.append(case_id)
        if case_id not in audit_times:
            issues.append(Issue("ERROR", case_id, "case_id is not in the request file"))
            continue
        issues += check_row(case_id, status, steps_json, audit_times[case_id], citation_times)

    missing = [case_id for case_id in audit_times if case_id not in seen]
    if missing:
        issues.append(Issue("ERROR", "-", f"{len(missing)} request(s) without a row, first: {missing[:5]}"))
    if seen != sorted(seen, key=_case_order):
        issues.append(Issue("ERROR", "-", "rows are not sorted by case_id"))
    return issues


def check_row(case_id: str, status: str, steps_json: str, audit_at: datetime,
              citation_times: dict[str, datetime]) -> list[Issue]:
    def error(message: str) -> Issue:
        return Issue("ERROR", case_id, message)

    if status not in STATUSES:
        return [error(f"deletion_status {status!r} is not allowed")]
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return [error(f"steps_json is not valid JSON ({exc.msg})")]
    if not isinstance(steps, list):
        return [error("steps_json is not a JSON array")]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else [error("NOT_APPLICABLE must have steps_json []")]

    issues: list[Issue] = []
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or set(step) != STEP_FIELDS:
            fields = sorted(step) if isinstance(step, dict) else type(step).__name__
            return [error(f"step {position} has fields {fields}, expected {sorted(STEP_FIELDS)}")]
        if type(step["step_no"]) is not int or step["step_no"] != position:
            issues.append(error(f"step {position} has step_no {step['step_no']!r}"))
        for name in STEP_FIELDS - {"step_no"}:
            if not isinstance(step[name], str):
                issues.append(error(f"step {position} field {name} is not a string"))
        if issues:
            return issues

    ranks = [PHASES.index(step["phase"]) if step["phase"] in PHASES else -1 for step in steps]
    for position, (step, rank) in enumerate(zip(steps, ranks), start=1):
        expected = OPERATIONS.get(step["operation"])
        if rank < 0:
            issues.append(error(f"step {position} has unknown phase {step['phase']!r}"))
        elif expected != (step["phase"], step["state"]):
            issues.append(error(f"step {position} ({step['phase']}, {step['operation']}, {step['state']}) "
                                "is not an allowed triple"))
        if not step["system_id"] or not step["resource_id"]:
            issues.append(error(f"step {position} has an empty system_id or resource_id"))
        issues += _check_evidence(case_id, position, step, status, audit_at, citation_times)
    if ranks != sorted(ranks):
        issues.append(error("phases are not in FINDING, REMEDIATION, VERIFICATION order"))
    if status == "COMPLETE" and any(step["phase"] != "VERIFICATION" for step in steps):
        issues.append(error("COMPLETE may contain VERIFICATION steps only"))
    if issues:
        return issues
    return _check_plan(case_id, status, steps)


def _check_evidence(case_id: str, position: int, step: dict, status: str, audit_at: datetime,
                    citation_times: dict[str, datetime]) -> list[Issue]:
    evidence_id = step["evidence_id"]
    cited = step["phase"] == "FINDING" or status == "COMPLETE"
    if not cited:
        if evidence_id:
            return [Issue("ERROR", case_id, f"step {position} ({step['phase']}) in an INCOMPLETE case "
                                            f"must have an empty evidence_id, has {evidence_id!r}")]
        return []
    if not evidence_id:
        return [Issue("ERROR", case_id, f"step {position} ({step['phase']}) must cite evidence")]
    available_at = citation_times.get(evidence_id)
    if available_at is None:
        return [Issue("ERROR", case_id, f"step {position} cites {evidence_id!r}, which does not exist")]
    if available_at > audit_at:
        return [Issue("ERROR", case_id, f"step {position} cites {evidence_id}, available at "
                                        f"{available_at.isoformat()}, after audit_at {audit_at.isoformat()}")]
    return []


def _check_plan(case_id: str, status: str, steps: list[dict]) -> list[Issue]:
    """Contract rules across steps: remediation order, verification coverage, and the shape of
    each verdict. Breaking §6.5 is an ERROR; an unusual but legal shape is a WARNING."""
    issues: list[Issue] = []
    remediation = [step for step in steps if step["phase"] == "REMEDIATION"]
    guards = [i for i, step in enumerate(remediation) if step["operation"] in GUARD_OPERATIONS]
    changes = [i for i, step in enumerate(remediation) if step["operation"] not in GUARD_OPERATIONS]
    if guards and changes and max(guards) > min(changes):
        issues.append(Issue("ERROR", case_id, "a record is changed before every data flow is disabled "
                                              "and every restore blocked"))
    verified = {(s["system_id"], s["resource_id"], s["state"]) for s in steps if s["phase"] == "VERIFICATION"}
    for step in remediation:
        if (step["system_id"], step["resource_id"], step["state"]) not in verified:
            issues.append(Issue("ERROR", case_id, f"remediation {step['operation']} of {step['resource_id']} "
                                                  f"has no verification of state {step['state']}"))
    keys = Counter((s["phase"], s["system_id"], s["resource_id"], s["operation"]) for s in steps)
    for key, count in keys.items():
        if count > 1:
            issues.append(Issue("WARNING", case_id, f"step {key} appears {count} times"))
    if status == "COMPLETE" and not steps:
        issues.append(Issue("WARNING", case_id, "COMPLETE with no verification step"))
    if status == "INCOMPLETE" and not any(step["phase"] == "FINDING" for step in steps):
        issues.append(Issue("WARNING", case_id, "INCOMPLETE with no finding"))
    return issues


def _case_order(case_id: str) -> tuple[int, str]:
    match = _NUMBER.search(case_id)
    return (int(match.group(1)) if match else -1, case_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--requests", type=Path, default=REPO_ROOT / "dataset" / "deletion_requests.csv")
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "dataset")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    audit_times = load_audit_times(args.requests)
    text = args.output.read_text(encoding="utf-8")
    issues = check_output(text, audit_times, load_citation_times(args.dataset))
    for issue in issues:
        print(issue)
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    statuses = Counter(row.get("deletion_status") for row in rows)
    errors = sum(issue.level == "ERROR" for issue in issues)
    print(f"{args.output.name}: {len(rows)} rows for {len(audit_times)} requests; "
          + ", ".join(f"{name}={count}" for name, count in sorted(statuses.items(), key=str))
          + f"; errors={errors}, warnings={len(issues) - errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
