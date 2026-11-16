"""A Resource's state at the Audit Time from what the case may know (FR-7, ADR-0005, D1, D2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from deletion_audit.evidence import EvidenceView
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.model import Observation, ResourceKey, SourceKind

_CONTENT_CONDITIONS = frozenset({"IDENTIFIABLE", "REDACTED", "ANONYMIZED"})


class ResourceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    WEAK_ABSENT = "WEAK_ABSENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ResourceState:
    resource: ResourceKey
    status: ResourceStatus
    observations: tuple[Observation, ...]
    content: tuple[Observation, ...]  # present entries that say what the record holds, oldest first
    presence_proof: str | None
    absence_proof: str | None
    flow_enabled: bool | None
    flow_proof: str | None
    restore_allowed: bool | None
    restore_proof: str | None


def resolve_state(resource: ResourceKey, view: EvidenceView) -> ResourceState:
    observations = view.observations_of(resource)
    items = [o for o in observations if o.source_kind is SourceKind.EVIDENCE_ITEM]
    ranked = items or list(observations)  # check Activities only when no Evidence Item covers it

    latest_by_copy: dict[str, Observation] = {}
    for observation in ranked:
        if observation.present is not None:
            latest_by_copy[observation.copy_location] = observation  # ascending order: last wins
    absence_proof = None
    if any(o.present for o in latest_by_copy.values()):
        status = ResourceStatus.PRESENT
    elif latest_by_copy:
        status = ResourceStatus.ABSENT
        absence_proof = (latest_citable(view, (o for o in ranked if o.present is False))
                         or _omitting_snapshot(resource, view, ranked))
    else:
        omitted = [s for s in view.snapshots_of(resource.system_id) if resource.resource_id not in s.resource_ids]
        status = ResourceStatus.WEAK_ABSENT if omitted else ResourceStatus.UNRESOLVED
        citable = [s for s in omitted if view.is_citable(s.citation_id)]
        absence_proof = citable[-1].citation_id if citable else None

    flow, flow_proof = _attribute(view, items, observations, "flow_enabled")
    restore, restore_proof = _attribute(view, items, observations, "restore_allowed")
    return ResourceState(
        resource=resource,
        status=status,
        observations=tuple(observations),
        content=tuple(o for o in ranked if o.present and _speaks_about_content(o)),
        presence_proof=latest_citable(view, (o for o in ranked if o.present)),
        absence_proof=absence_proof,
        flow_enabled=flow,
        flow_proof=flow_proof,
        restore_allowed=restore,
        restore_proof=restore_proof,
    )


def latest_citable(view: EvidenceView, observations) -> str | None:
    citable = [o.citation_id for o in observations if view.is_citable(o.citation_id)]
    return citable[-1] if citable else None


def _omitting_snapshot(resource: ResourceKey, view: EvidenceView, ranked) -> str | None:
    """D13: when the absence is stated only by uncitable items, a citable snapshot of the
    System that omits the record proves it, if it was taken after the record was last seen."""
    last_seen = max((o.known_at for o in ranked if o.present), default=None)
    omitted = [s for s in view.snapshots_of(resource.system_id)
               if resource.resource_id not in s.resource_ids and view.is_citable(s.citation_id)
               and (last_seen is None or s.known_at > last_seen)]
    return max(omitted, key=lambda s: (s.known_at, s.citation_id)).citation_id if omitted else None


def _attribute(view: EvidenceView, items, observations, name: str) -> tuple[bool | None, str | None]:
    """Latest value from Evidence Items, falling back to check Activities (ADR-0002)."""
    for pool in (items, observations):
        valued = [o for o in pool if getattr(o, name) is not None]
        if valued:
            value = getattr(valued[-1], name)
            return value, latest_citable(view, (o for o in valued if getattr(o, name) == value))
    return None, None


def _speaks_about_content(observation: Observation) -> bool:
    if observation.stated_condition in _CONTENT_CONDITIONS:
        return True
    return any(trait.removeprefix("field:") in vocab.CONTENT_FIELDS
               for trait in observation.traits if trait.startswith("field:"))
