"""Turns each Relevant Resource's state and Treatment into contract steps (FR-9, D5-D8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from deletion_audit.assessment.policy import Treatment
from deletion_audit.assessment.state import ResourceState, ResourceStatus, latest_citable
from deletion_audit.evidence import EvidenceView
from deletion_audit.model import CaseResult, Phase, ResourceKey, Step, Verdict
from deletion_audit.submission import OPERATIONS, step_sort_key

_STATE = {operation: state for pairs in OPERATIONS.values() for operation, state in pairs}
_ABSENT = (ResourceStatus.ABSENT, ResourceStatus.WEAK_ABSENT)


@dataclass(frozen=True)
class Plan:
    """One Resource's contribution: an optional cited Finding, and its final Verification."""

    resource: ResourceKey
    treatment: Treatment
    finding: str | None = None
    remediation: str | None = None
    finding_proof: str | None = None
    verification: str | None = None
    verification_proof: str | None = None
    blocks_completion: bool = False  # a violation or missing retained record without a Finding
    note: str | None = None


@dataclass(frozen=True)
class Exposure:
    direct: frozenset[str]
    user_tag: str
    view: EvidenceView = field(repr=False)


def plan_resource(state: ResourceState, treatment: Treatment, exposure: Exposure) -> Plan:
    resource, status = state.resource, state.status
    if treatment is Treatment.DELETE:
        if status is ResourceStatus.PRESENT:
            return _violation(resource, treatment, "REPORT_REMAINING_RECORD", "DELETE_RECORD",
                              state.presence_proof, "VERIFY_RECORD_ABSENT")
        return _compliant(resource, treatment, "VERIFY_RECORD_ABSENT",
                          state.absence_proof if status in _ABSENT else None)
    if treatment in (Treatment.REDACT, Treatment.ANONYMIZE):
        verify = "VERIFY_RECORD_REDACTED" if treatment is Treatment.REDACT else "VERIFY_RECORD_ANONYMIZED"
        if status in _ABSENT:
            return Plan(resource, treatment, note="D6: absent although policy requires it to be kept")
        if status is ResourceStatus.UNRESOLVED or not state.content:
            return _compliant(resource, treatment, verify, None)
        exposed = [o for o in state.content if _is_exposed(o, exposure, treatment)]
        if exposed and exposed[-1] is state.content[-1]:
            remediation = "REDACT_RECORD" if treatment is Treatment.REDACT else "ANONYMIZE_RECORD"
            return _violation(resource, treatment, "REPORT_EXPOSED_PII", remediation,
                              latest_citable(exposure.view, exposed), verify)
        clean = [o for o in state.content if not _is_exposed(o, exposure, treatment)]
        return _compliant(resource, treatment, verify, latest_citable(exposure.view, clean))
    if treatment is Treatment.RETAIN:
        if status is ResourceStatus.ABSENT:
            return Plan(resource, treatment, verification="VERIFY_RECORD_PRESENT", blocks_completion=True,
                        note="D5: retained record is missing")
        proof = state.presence_proof if status is ResourceStatus.PRESENT else None
        return _compliant(resource, treatment, "VERIFY_RECORD_PRESENT", proof)
    if treatment is Treatment.BLOCK_RESTORE:
        if state.restore_allowed:
            return _violation(resource, treatment, "REPORT_RESTORE_RISK", "BLOCK_RESTORE",
                              state.restore_proof, "VERIFY_RESTORE_BLOCKED")
        # A backup can be restored until something blocks it: a present archive whose restore
        # access no evidence states is still a restore path, proved by the archive itself.
        if state.restore_allowed is None and status is ResourceStatus.PRESENT:
            return _violation(resource, treatment, "REPORT_RESTORE_RISK", "BLOCK_RESTORE",
                              state.presence_proof, "VERIFY_RESTORE_BLOCKED")
        proof = state.restore_proof if state.restore_allowed is False else None
        return _compliant(resource, treatment, "VERIFY_RESTORE_BLOCKED", proof)
    if state.flow_enabled:
        return _violation(resource, treatment, "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW",
                          state.flow_proof, "VERIFY_DATA_FLOW_DISABLED")
    proof = state.flow_proof if state.flow_enabled is False else None
    return _compliant(resource, treatment, "VERIFY_DATA_FLOW_DISABLED", proof)


def build_result(case_id: str, plans: Sequence[Plan]) -> tuple[CaseResult, list[str]]:
    notes = [f"{p.resource.resource_id}: {p.note}" for p in plans if p.note]
    findings = [p for p in plans if p.finding]
    relevant = [p for p in plans if p.verification]
    if not plans:
        return CaseResult(case_id, Verdict.INCOMPLETE, ()), ["unlinked"]
    complete = not findings and not any(p.blocks_completion for p in plans)
    steps: list[Step] = []
    if complete:
        for p in relevant:
            if p.verification_proof:
                steps.append(_step(Phase.VERIFICATION, p.resource, p.verification, p.verification_proof))
            else:
                notes.append(f"{p.resource.resource_id}: D7 unresolved, verification omitted")
        return CaseResult(case_id, Verdict.COMPLETE, _sorted(steps)), notes
    for p in findings:
        steps.append(_step(Phase.FINDING, p.resource, p.finding, p.finding_proof))
    # Remediation is future work and needs no evidence, so a violation known only from an
    # uncitable item still gets its remediation; only the Finding is left out.
    for p in plans:
        if p.remediation:
            steps.append(_step(Phase.REMEDIATION, p.resource, p.remediation, ""))
    for p in relevant:
        steps.append(_step(Phase.VERIFICATION, p.resource, p.verification, ""))
    return CaseResult(case_id, Verdict.INCOMPLETE, _sorted(steps)), notes


def _violation(resource, treatment, finding, remediation, proof, verification) -> Plan:
    if proof is None:
        return Plan(resource, treatment, remediation=remediation, verification=verification,
                    blocks_completion=True, note=f"{finding} known but not citable")
    return Plan(resource, treatment, finding=finding, remediation=remediation, finding_proof=proof,
                verification=verification)


def _compliant(resource, treatment, verification, proof) -> Plan:
    return Plan(resource, treatment, verification=verification, verification_proof=proof)


def _is_exposed(observation, exposure: Exposure, treatment: Treatment) -> bool:
    if observation.stated_condition == "IDENTIFIABLE":
        return True
    if observation.stated_condition in ("REDACTED", "ANONYMIZED"):
        return False
    if observation.exposed & exposure.direct:
        return True
    return treatment is Treatment.ANONYMIZE and exposure.user_tag in observation.references


def _step(phase: Phase, resource: ResourceKey, operation: str, evidence_id: str) -> Step:
    return Step(phase, resource.system_id, resource.resource_id, operation, _STATE[operation], evidence_id)


def _sorted(steps: list[Step]) -> tuple[Step, ...]:
    return tuple(sorted(steps, key=step_sort_key))
