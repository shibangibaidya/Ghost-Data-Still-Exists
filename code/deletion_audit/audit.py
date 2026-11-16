"""`run_audit`: the single entry point that turns a request file into one result per case."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deletion_audit.assessment import Assessment, assess_case
from deletion_audit.assessment.policy import POLICY_ACTIVITIES, RuleBook, known_policy_actions
from deletion_audit.dataset import Dataset, load_dataset, read_cases
from deletion_audit.evidence import build_evidence_index
from deletion_audit.evidence.visual import VisualReader
from deletion_audit.identity import IdentityDirectory
from deletion_audit.intent import IntentDecision, classify_intents
from deletion_audit.linking import link_cases
from deletion_audit.llm import LlmClient
from deletion_audit.model import Case, CaseResult, Link, Subject, Verdict
from deletion_audit.submission import fallback_result

Trace = dict[str, Any]
VERIFICATION_PATH = Path(__file__).resolve().parents[1] / "cache" / "visual_verification.json"


@dataclass(frozen=True, slots=True)
class AuditRun:
    dataset: Dataset
    cases: tuple[Case, ...]
    results: tuple[CaseResult, ...]
    traces: dict[str, Trace]
    failures: tuple[tuple[str, str], ...]
    links: dict[str, tuple[Link, ...]]


def run_audit(
    dataset_root: Path, requests_path: Path, llm: LlmClient, *, intent_model: str, visual_model: str,
    require_verified: bool = True, verification_path: Path = VERIFICATION_PATH,
) -> AuditRun:
    dataset = load_dataset(dataset_root)
    cases = read_cases(requests_path)
    official = (*dataset.sample_cases, *dataset.cases)
    decisions = classify_intents([case.request_text for case in (*cases, *official)], llm, model=intent_model)
    directory = IdentityDirectory(dataset.identities, dataset.identity_changes)
    visual = VisualReader(llm, visual_model, verification_path, require_verified)
    index = build_evidence_index(dataset, visual)
    subjects = {case.case_id: directory.resolve(case.request_email, case.audit_time) for case in cases}
    intents = {case.case_id: decisions[case.request_text].has_deletion_intent for case in cases}
    deletion_cases = [case for case in cases if intents[case.case_id]]
    # Every official request with Deletion Intent competes for Bursts, whichever file is predicted.
    targets = {case.case_id for case in cases}
    context = [
        (case, directory.resolve(case.request_email, case.audit_time))
        for case in official
        if case.case_id not in targets and decisions[case.request_text].has_deletion_intent
    ]
    book = link_cases([(case, subjects[case.case_id]) for case in deletion_cases], index,
                      dataset.activities, context=context)
    rules = RuleBook.from_rules(dataset.rules)
    policy_activities = tuple(a for a in dataset.activities if a.event_type in POLICY_ACTIVITIES)

    results: list[CaseResult] = []
    traces: dict[str, Trace] = {}
    failures: list[tuple[str, str]] = []
    for case in cases:
        trace: Trace = {
            "case_id": case.case_id,
            "notes": [],
            "subject": _subject_trace(subjects[case.case_id]),
            "links": [_link_trace(link) for link in book.links(case.case_id)],
            "intent": _intent_trace(decisions[case.request_text]),
        }
        try:
            if intents[case.case_id]:
                assessment = assess_case(case, subjects[case.case_id], book.links(case.case_id),
                                         index.for_case(case), rules,
                                         known_policy_actions(policy_activities, case))
                trace.update(_assessment_trace(assessment))
                result = assessment.result
            else:
                result = CaseResult(case.case_id, Verdict.NOT_APPLICABLE, ())
        except Exception as error:  # one broken case must not cost the other rows (FR-10)
            failures.append((case.case_id, repr(error)))
            trace["error"] = repr(error)
            result = fallback_result(case.case_id)
        trace["verdict"] = result.verdict.value
        traces[case.case_id] = trace
        results.append(result)
    return AuditRun(dataset, cases, tuple(results), traces, tuple(failures), links=book.as_dict())


def _intent_trace(decision: IntentDecision) -> Trace:
    return {"deletion": decision.has_deletion_intent, "request_type": decision.request_type,
            "language": decision.language}


def _assessment_trace(assessment: Assessment) -> Trace:
    return {
        "notes": list(assessment.notes),
        "resources": [
            {
                "resource": f"{item.link.resource.system_id}/{item.link.resource.resource_id}",
                "category": item.category,
                "status": item.state.status.value,
                "treatment": item.plan.treatment.value,
                "finding": item.plan.finding,
                "verification": item.plan.verification,
                "proof": item.plan.finding_proof or item.plan.verification_proof,
                "observations": [o.citation_id for o in item.state.observations],
            }
            for item in assessment.resources
        ],
    }


def _link_trace(link: Link) -> Trace:
    return {"resource": f"{link.resource.system_id}/{link.resource.resource_id}",
            "basis": link.basis.value, "support": list(link.support)}


def _subject_trace(subject: Subject | None) -> Trace | None:
    if subject is None:
        return None
    identifiers = subject.identifiers
    return {
        "user_id": subject.user_id,
        "emails": sorted(identifiers.emails),
        "phones": sorted(identifiers.phones),
        "email_hashes": sorted(identifiers.email_hashes),
        "names": sorted(identifiers.names),
        "ambiguous_with": list(subject.ambiguous_with),
    }
