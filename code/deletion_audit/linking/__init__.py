"""Links Resources to each case's Subject (DESIGN.md M6). Passes run in a fixed order over the
case's own view: Identifier Match, Reference Match, Lineage to a fixed point, then Burst
Attribution against the other claimants of the same report slot (ADR-0001)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from deletion_audit.dataset import Activity
from deletion_audit.evidence import EvidenceIndex, EvidenceView, identifier_tags
from deletion_audit.linking.bursts import burst_links
from deletion_audit.linking.lineage import activity_lineage, lineage_links
from deletion_audit.model import Case, Link, LinkBasis, ResourceKey, Subject

_STRENGTH = {basis: rank for rank, basis in enumerate(LinkBasis)}

Found = dict[ResourceKey, tuple[LinkBasis, set[str]]]


@dataclass(frozen=True)
class LinkBook:
    _links: dict[str, tuple[Link, ...]]

    def links(self, case_id: str) -> tuple[Link, ...]:
        return self._links.get(case_id, ())

    def as_dict(self) -> dict[str, tuple[Link, ...]]:
        return dict(self._links)


def link_cases(
    case_subjects: Sequence[tuple[Case, Subject | None]],
    index: EvidenceIndex,
    activities: Iterable[Activity],
    *,
    episode: bool = True,
    context: Sequence[tuple[Case, Subject | None]] = (),
) -> LinkBook:
    """`case_subjects` are the cases to link; `context` adds every other case with Deletion
    Intent, so Bursts are shared out against all claimants and never by input order."""
    activities = tuple(activities)
    claimants = {case.case_id: (case, subject) for case, subject in (*context, *case_subjects) if subject}
    by_slot: dict[object, list[tuple[Case, Subject]]] = defaultdict(list)
    for case, subject in sorted(claimants.values(), key=lambda pair: pair[0].case_id):
        by_slot[case.reported_at].append((case, subject))
    slots = set(by_slot)

    found = {}
    for case, subject in sorted(case_subjects, key=lambda pair: pair[0].case_id):
        if subject is None:
            found[case.case_id] = ()
            continue
        view = index.for_case(case, episode=episode)
        known = [a for a in activities if _known(a, case, episode)]
        best = _direct_links(subject, view, known)
        rivals = [
            ((other, other_subject), set(_direct_links(other_subject, view, known)))
            for other, other_subject in by_slot[case.reported_at]
            if other_subject.user_id != subject.user_id  # same person, same slot: one claimant
        ]
        for resource, citation in burst_links(case, subject, set(best), rivals, known, slots):
            _add(best, resource, LinkBasis.BURST, citation)
        found[case.case_id] = tuple(
            Link(resource, basis, tuple(sorted(citations)))
            for resource, (basis, citations) in sorted(best.items())
        )
    return LinkBook(found)


def _direct_links(subject: Subject, view: EvidenceView, known: Sequence[Activity]) -> Found:
    direct_tags = identifier_tags(subject.identifiers) - {f"user:{subject.user_id}"}
    reference_tag = f"user:{subject.user_id}"
    best: Found = {}
    for observation in view.observations_matching(subject.identifiers):
        if observation.identifiers & direct_tags:
            _add(best, observation.resource, LinkBasis.IDENTIFIER, observation.citation_id)
        elif reference_tag in observation.identifiers:
            _add(best, observation.resource, LinkBasis.REFERENCE, observation.citation_id)
    for resource, citation in activity_lineage(known, subject):
        _add(best, resource, LinkBasis.LINEAGE, citation)
    for resource, citation in lineage_links(set(best), view.all_observations(), known):
        _add(best, resource, LinkBasis.LINEAGE, citation)
    return best


def _add(best: Found, resource: ResourceKey, basis: LinkBasis, citation: str) -> None:
    current = best.get(resource)
    if current is None or _STRENGTH[basis] < _STRENGTH[current[0]]:
        best[resource] = (basis, {citation})
    elif current[0] is basis:
        current[1].add(citation)


def _known(activity: Activity, case: Case, episode: bool) -> bool:
    start_ok = not episode or activity.event_time >= case.requested_at
    return start_ok and activity.event_time <= case.audit_time
