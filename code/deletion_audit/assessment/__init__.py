"""`assess_case`: one case's Verdict and steps from its Links and what it may know (M7)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deletion_audit.assessment.planning import Exposure, Plan, build_result, plan_resource
from deletion_audit.assessment.policy import RuleBook
from deletion_audit.assessment.state import ResourceState, resolve_state
from deletion_audit.evidence import EvidenceView, identifier_tags
from deletion_audit.model import Case, CaseResult, Link, Subject


@dataclass(frozen=True)
class ResourceAssessment:
    link: Link
    category: str
    state: ResourceState
    plan: Plan


@dataclass(frozen=True)
class Assessment:
    result: CaseResult
    resources: tuple[ResourceAssessment, ...]
    notes: tuple[str, ...]


def assess_case(
    case: Case, subject: Subject | None, links: Sequence[Link], view: EvidenceView, rules: RuleBook,
    actions: Mapping[str, str] | None = None,
) -> Assessment:
    """`actions`: the worker's policy action per resource_id within the Episode (D14)."""
    if subject is None:
        result, notes = build_result(case.case_id, ())
        return Assessment(result, (), ("no subject for the request email", *notes))
    user_tag = f"user:{subject.user_id}"
    direct = identifier_tags(subject.identifiers) - {user_tag}
    direct |= {f"name:{name}" for name in subject.identifiers.names}
    exposure = Exposure(frozenset(direct), user_tag, view)

    assessed = []
    for link in links:
        state = resolve_state(link.resource, view)
        action = (actions or {}).get(link.resource.resource_id)
        category = rules.category(link.resource, state.observations, action)
        plan = plan_resource(state, rules.treatment(category), exposure)
        assessed.append(ResourceAssessment(link, category, state, plan))
    result, notes = build_result(case.case_id, [item.plan for item in assessed])
    return Assessment(result, tuple(assessed), tuple(notes))
