"""Lineage: Resources derived from, or catalogued against, already Linked Resources."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from deletion_audit.dataset import Activity
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.evidence.entries import extract_tokens
from deletion_audit.evidence import identifier_tags
from deletion_audit.model import Observation, ResourceKey, Subject

_DOWNSTREAM = re.compile(r"repopulated downstream resource (\S+?)\.?$")


def activity_lineage(activities: Iterable[Activity], subject: Subject) -> Iterator[tuple[ResourceKey, str]]:
    """Catalog activities that tie a resource to the Subject's identifiers."""
    tags = identifier_tags(subject.identifiers)
    for activity in activities:
        if activity.event_type == "LINK_RESOURCE_IDENTITY" and extract_tokens(activity.details) & tags:
            yield ResourceKey(vocab.system_for(activity.resource_id), activity.resource_id), activity.activity_id


def lineage_links(
    linked: set[ResourceKey], observations: Iterable[Observation], activities: Iterable[Activity]
) -> list[tuple[ResourceKey, str]]:
    """Resources connected to `linked` through evidence lineage or downstream-copy activities,
    followed in both directions until nothing new is reached."""
    edges: list[tuple[ResourceKey, ResourceKey, str]] = []
    for observation in observations:
        for other in observation.lineage:
            edges.append((observation.resource, other, observation.citation_id))
    for activity in activities:
        match = _DOWNSTREAM.search(activity.details)
        if match and vocab.RESOURCE_ID.fullmatch(match.group(1)):
            source = ResourceKey(vocab.system_for(activity.resource_id), activity.resource_id)
            target = ResourceKey(vocab.system_for(match.group(1)), match.group(1))
            edges.append((source, target, activity.activity_id))

    reached = set(linked)
    found: list[tuple[ResourceKey, str]] = []
    changed = True
    while changed:
        changed = False
        for left, right, citation in sorted(edges, key=lambda edge: (edge[0], edge[1], edge[2])):
            for start, end in ((left, right), (right, left)):
                if start in reached and end not in reached:
                    reached.add(end)
                    found.append((end, citation))
                    changed = True
    return found
