"""Evidence index: every evidence format reduced to Observations and Snapshots, read per case
through a view that applies the Known and Episode gates (ADR-0005, D9)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from deletion_audit.dataset import Activity, Dataset, ManifestEntry
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.evidence.entries import Entry, to_observation
from deletion_audit.evidence.json_records import parse_json
from deletion_audit.evidence.logs import parse_log, parse_text
from deletion_audit.evidence.sql import parse_sql
from deletion_audit.evidence.visual import VisualReader, catalog_aliases
from deletion_audit.model import Case, IdentifierSet, Observation, ResourceKey, Snapshot, SourceKind

VISUAL_SUFFIXES = (".png", ".pdf")


def identifier_tags(identifiers: IdentifierSet) -> frozenset[str]:
    """Subject identifiers as Observation tokens. Names are left out: they never Link alone."""
    return frozenset(
        {f"email:{value}" for value in identifiers.emails}
        | {f"phone:{value}" for value in identifiers.phones}
        | {f"hash:{value}" for value in identifiers.email_hashes}
        | {f"user:{identifiers.user_id}"}
    )


class EvidenceView:
    """What one case may know: entries with requested_at <= known_at <= audit_at."""

    def __init__(self, index: EvidenceIndex, case: Case, episode: bool) -> None:
        start = case.requested_at if episode else datetime.min.replace(tzinfo=case.audit_time.tzinfo)
        self._audit_time = case.audit_time
        self._citation_time = index.citation_time
        self._observations = tuple(o for o in index.all_observations() if start <= o.known_at <= case.audit_time)
        self._snapshots = tuple(s for s in index.all_snapshots() if start <= s.known_at <= case.audit_time)
        self._by_resource: dict[ResourceKey, list[Observation]] = defaultdict(list)
        for observation in self._observations:
            self._by_resource[observation.resource].append(observation)

    def all_observations(self) -> tuple[Observation, ...]:
        return self._observations

    def observations_of(self, resource: ResourceKey) -> tuple[Observation, ...]:
        return tuple(self._by_resource.get(resource, ()))

    def observations_matching(self, identifiers: IdentifierSet) -> tuple[Observation, ...]:
        tags = identifier_tags(identifiers)
        return tuple(o for o in self._observations if o.identifiers & tags)

    def snapshots_of(self, system_id: str) -> tuple[Snapshot, ...]:
        return tuple(s for s in self._snapshots if s.system_id == system_id)

    def is_citable(self, citation_id: str) -> bool:
        cited_at = self._citation_time(citation_id)
        return cited_at is not None and cited_at <= self._audit_time


@dataclass(frozen=True)
class EvidenceIndex:
    observations: tuple[Observation, ...]
    snapshots: tuple[Snapshot, ...]
    parsed_files: tuple[str, ...]
    skipped_files: tuple[str, ...]
    citation_time: Callable[[str], datetime | None]

    def all_observations(self) -> tuple[Observation, ...]:
        return self.observations

    def all_snapshots(self) -> tuple[Snapshot, ...]:
        return self.snapshots

    def for_case(self, case: Case, *, episode: bool = True) -> EvidenceView:
        return EvidenceView(self, case, episode)


def build_evidence_index(dataset: Dataset, visual: VisualReader | None = None) -> EvidenceIndex:
    """Without a `visual` reader, PNG and PDF files are skipped (reported in `skipped_files`)."""
    entries: list[Entry] = []
    snapshots: list[Snapshot] = []
    parsed, skipped = [], []
    aliases = catalog_aliases(dataset.activities)
    for manifest in dataset.manifest:
        if manifest.path.suffix.lower() in VISUAL_SUFFIXES:
            if visual is None:
                skipped.append(manifest.file_id)
            else:
                entries += visual.entries(manifest, aliases)
                parsed.append(manifest.file_id)
            continue
        file_entries, file_snapshots = _parse(manifest)
        entries += file_entries
        snapshots += file_snapshots
        parsed.append(manifest.file_id)
    observations = [to_observation(entry) for entry in entries]
    observations += check_observations(dataset.activities)
    observations.sort(key=lambda o: (o.known_at, o.citation_id, o.ordinal, o.resource))
    snapshots.sort(key=lambda s: (s.known_at, s.citation_id, s.system_id, s.copy_location, s.collection))
    return EvidenceIndex(tuple(observations), tuple(snapshots), tuple(parsed), tuple(skipped),
                         dataset.citation_time)


def check_observations(activities: Iterable[Activity]) -> list[Observation]:
    """Read-only check Activities as low-rank Observations (ADR-0002, D1)."""
    found = []
    for activity in activities:
        check = vocab.CHECK_PHRASES.get(activity.event_type)
        if check is None or activity.reported_result != "SUCCESS":
            continue
        phrase, attribute = check
        confirmed = phrase in activity.details
        resource = ResourceKey(vocab.system_for(activity.resource_id), activity.resource_id)
        found.append(Observation(
            citation_id=activity.activity_id, source_kind=SourceKind.CHECK_ACTIVITY,
            citable_at=activity.event_time, known_at=activity.event_time, ordinal=0, explicit=True,
            resource=resource, copy_location="primary", present=True,
            identifiers=frozenset(), exposed=frozenset(), references=frozenset(),
            stated_category=None, stated_condition=None,
            flow_enabled=confirmed if attribute == "flow_enabled" else None,
            restore_allowed=confirmed if attribute == "restore_allowed" else None,
            retained_until=None, lineage=frozenset(), traits=frozenset({f"event:{activity.event_type}"}),
        ))
    return found


def _parse(manifest: ManifestEntry) -> tuple[list[Entry], list[Snapshot]]:
    suffix = manifest.path.suffix.lower()
    if suffix == ".sql":
        return parse_sql(manifest)
    if suffix == ".json":
        return parse_json(manifest)
    if suffix == ".log":
        return parse_log(manifest), []
    if suffix == ".txt":
        return parse_text(manifest), []
    raise ValueError(f"{manifest.file_id}: unsupported evidence format {suffix}")
