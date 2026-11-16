"""The output contract (AGENTS.md §6.3-6.6): rendering, parsing, checking and finalizing rows."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from deletion_audit.dataset import case_order
from deletion_audit.model import Case, CaseResult, Phase, Step, Verdict

HEADER = ("case_id", "deletion_status", "steps_json")

# Allowed operations per phase, in contract order, with the state each one implies.
OPERATIONS: dict[Phase, tuple[tuple[str, str], ...]] = {
    Phase.FINDING: (
        ("REPORT_REMAINING_RECORD", "PRESENT"),
        ("REPORT_EXPOSED_PII", "PRESENT"),
        ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
        ("REPORT_RESTORE_RISK", "ACTIVE"),
    ),
    Phase.REMEDIATION: (
        ("DISABLE_DATA_FLOW", "DISABLED"),
        ("BLOCK_RESTORE", "BLOCKED"),
        ("DELETE_RECORD", "ABSENT"),
        ("REDACT_RECORD", "REDACTED"),
        ("ANONYMIZE_RECORD", "ANONYMIZED"),
    ),
    Phase.VERIFICATION: (
        ("VERIFY_RECORD_ABSENT", "ABSENT"),
        ("VERIFY_RECORD_PRESENT", "PRESENT"),
        ("VERIFY_RECORD_REDACTED", "REDACTED"),
        ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
        ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        ("VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    ),
}
_PHASE_RANK = {phase: rank for rank, phase in enumerate(Phase)}
_OPERATION_RANK = {
    (phase, operation): rank
    for phase, pairs in OPERATIONS.items()
    for rank, (operation, _) in enumerate(pairs)
}
_STATE_OF = {(phase, operation): state for phase, pairs in OPERATIONS.items() for operation, state in pairs}

CitationTime = Callable[[str], datetime | None]


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class ContractViolation:
    case_id: str
    severity: Severity
    rule: str
    message: str


@dataclass(frozen=True, slots=True)
class FinalRows:
    results: tuple[CaseResult, ...]
    replaced: tuple[str, ...]
    violations: tuple[ContractViolation, ...]


def step_sort_key(step: Step) -> tuple[int, int, str, str]:
    """Contract order: phase, operation table order, then system_id and resource_id (ADR-0003)."""
    return (
        _PHASE_RANK[step.phase],
        _OPERATION_RANK.get((step.phase, step.operation), len(_OPERATION_RANK)),
        step.system_id,
        step.resource_id,
    )


def fallback_result(case_id: str) -> CaseResult:
    return CaseResult(case_id, Verdict.INCOMPLETE, ())


def render_submission(results: Sequence[CaseResult]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for result in sorted(results, key=lambda item: case_order(item.case_id)):
        writer.writerow((result.case_id, result.verdict.value, _steps_json(result.steps)))
    return buffer.getvalue()


def parse_submission(text: str) -> list[CaseResult]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != HEADER:
        raise ValueError(f"header must be {','.join(HEADER)}, got {reader.fieldnames}")
    results = []
    for row in reader:
        raw_steps = json.loads(row["steps_json"])
        numbers = [raw["step_no"] for raw in raw_steps]
        if numbers != list(range(1, len(raw_steps) + 1)):
            raise ValueError(f"{row['case_id']}: step_no must count from 1, got {numbers}")
        steps = tuple(
            Step(
                Phase(raw["phase"]), raw["system_id"], raw["resource_id"], raw["operation"],
                raw["state"], raw["evidence_id"],
            )
            for raw in raw_steps
        )
        results.append(CaseResult(row["case_id"], Verdict(row["deletion_status"]), steps))
    return results


def check_submission(
    results: Sequence[CaseResult], cases: Sequence[Case], citation_time: CitationTime
) -> list[ContractViolation]:
    by_id = {case.case_id: case for case in cases}
    produced = [result.case_id for result in results]
    violations = [
        ContractViolation(case_id, Severity.ERROR, "missing_case", "no row for this case")
        for case_id in sorted(set(by_id) - set(produced), key=case_order)
    ]
    violations += [
        ContractViolation(case_id, Severity.ERROR, "unexpected_case", "row for an unknown case")
        for case_id in sorted(set(produced) - set(by_id), key=case_order)
    ]
    violations += [
        ContractViolation(case_id, Severity.ERROR, "duplicate_case", "more than one row")
        for case_id in sorted({c for c in produced if produced.count(c) > 1}, key=case_order)
    ]
    for result in results:
        if result.case_id in by_id:
            violations += check_case(result, by_id[result.case_id], citation_time)
    return violations


def check_case(result: CaseResult, case: Case, citation_time: CitationTime) -> list[ContractViolation]:
    found: list[tuple[Severity, str, str]] = []
    steps = result.steps
    phases = {step.phase for step in steps}

    if result.verdict is Verdict.NOT_APPLICABLE and steps:
        found.append((Severity.ERROR, "not_applicable_steps", "NOT_APPLICABLE must have []"))
    if result.verdict is Verdict.COMPLETE and phases - {Phase.VERIFICATION}:
        found.append((Severity.ERROR, "complete_phases", "COMPLETE may contain only VERIFICATION"))
    if result.verdict is Verdict.INCOMPLETE and Phase.FINDING not in phases:
        found.append((Severity.WARNING, "incomplete_without_finding", "INCOMPLETE without a FINDING"))
    if result.verdict is Verdict.COMPLETE and Phase.VERIFICATION not in phases:
        found.append((Severity.WARNING, "complete_without_verification", "COMPLETE without VERIFICATION"))

    for step in steps:
        if _STATE_OF.get((step.phase, step.operation)) != step.state:
            found.append((Severity.ERROR, "invalid_triple",
                          f"{step.phase}/{step.operation}/{step.state} is not allowed"))
        found += [(Severity.ERROR, rule, message) for rule, message in _evidence_problems(step, result.verdict)]
        if step.evidence_id:
            cited_at = citation_time(step.evidence_id)
            if cited_at is None:
                found.append((Severity.ERROR, "unknown_citation", f"{step.evidence_id} does not exist"))
            elif cited_at > case.audit_time:
                found.append((Severity.ERROR, "citation_after_audit",
                              f"{step.evidence_id} is after audit_at"))

    for before, after in zip(steps, steps[1:]):
        if _PHASE_RANK[before.phase] > _PHASE_RANK[after.phase]:
            found.append((Severity.ERROR, "phase_order", f"{after.phase} after {before.phase}"))
        elif step_sort_key(before) > step_sort_key(after):
            found.append((Severity.ERROR, "sort_order",
                          f"{after.resource_id} must not follow {before.resource_id}"))

    return [ContractViolation(result.case_id, severity, rule, message) for severity, rule, message in found]


def finalize_rows(
    results: Sequence[CaseResult], cases: Sequence[Case], citation_time: CitationTime
) -> FinalRows:
    """One row per case: a case that is missing or breaks the contract gets the fallback row."""
    by_id = {result.case_id: result for result in results}
    final: list[CaseResult] = []
    replaced: list[str] = []
    violations: list[ContractViolation] = []
    for case in sorted(cases, key=lambda item: case_order(item.case_id)):
        result = by_id.get(case.case_id)
        problems = (
            check_case(result, case, citation_time)
            if result is not None
            else [ContractViolation(case.case_id, Severity.ERROR, "missing_case", "no row for this case")]
        )
        violations += problems
        if any(problem.severity is Severity.ERROR for problem in problems):
            replaced.append(case.case_id)
            result = fallback_result(case.case_id)
        final.append(result)
    return FinalRows(tuple(final), tuple(replaced), tuple(violations))


def _evidence_problems(step: Step, verdict: Verdict) -> list[tuple[str, str]]:
    if step.phase is Phase.FINDING and not step.evidence_id:
        return [("finding_evidence", "a FINDING must cite evidence")]
    if step.phase is Phase.REMEDIATION and step.evidence_id:
        return [("remediation_evidence", "a REMEDIATION must use an empty evidence_id")]
    if step.phase is Phase.VERIFICATION:
        if verdict is Verdict.COMPLETE and not step.evidence_id:
            return [("verification_evidence", "COMPLETE verification must cite evidence")]
        if verdict is Verdict.INCOMPLETE and step.evidence_id:
            return [("verification_evidence", "INCOMPLETE verification must use an empty evidence_id")]
    return []


def _steps_json(steps: Sequence[Step]) -> str:
    payload = [
        {
            "step_no": number,
            "phase": step.phase.value,
            "system_id": step.system_id,
            "resource_id": step.resource_id,
            "operation": step.operation,
            "state": step.state,
            "evidence_id": step.evidence_id,
        }
        for number, step in enumerate(steps, start=1)
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
