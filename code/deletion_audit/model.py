"""Domain types shared across module interfaces. Names follow GLOSSARY.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Verdict(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Phase(StrEnum):
    FINDING = "FINDING"
    REMEDIATION = "REMEDIATION"
    VERIFICATION = "VERIFICATION"


class LinkBasis(StrEnum):
    IDENTIFIER = "IDENTIFIER"
    REFERENCE = "REFERENCE"
    LINEAGE = "LINEAGE"
    BURST = "BURST"


class SourceKind(StrEnum):
    EVIDENCE_ITEM = "EVIDENCE_ITEM"
    CHECK_ACTIVITY = "CHECK_ACTIVITY"


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    request_email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_time: datetime
    request_text: str


@dataclass(frozen=True, slots=True)
class IdentifierSet:
    """Normalized Identifiers: emails lower-case, phones digits only, hashes lower-case."""

    user_id: str
    emails: frozenset[str]
    phones: frozenset[str]
    email_hashes: frozenset[str]
    names: frozenset[str]


@dataclass(frozen=True, slots=True)
class Subject:
    user_id: str
    identifiers: IdentifierSet
    ambiguous_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class ResourceKey:
    system_id: str
    resource_id: str


@dataclass(frozen=True, slots=True)
class Observation:
    citation_id: str
    source_kind: SourceKind
    citable_at: datetime
    known_at: datetime
    ordinal: int
    explicit: bool
    resource: ResourceKey
    copy_location: str
    present: bool | None
    # Identifier tokens are tagged by kind: "email:", "phone:", "hash:", "name:", "user:".
    identifiers: frozenset[str]  # every identifier in the entry, including lookup keys
    exposed: frozenset[str]      # direct identifiers held in the record's own content fields
    references: frozenset[str]   # Stable References ("user:" tokens) the record itself holds
    stated_category: str | None
    stated_condition: str | None
    flow_enabled: bool | None
    restore_allowed: bool | None
    retained_until: datetime | None
    lineage: frozenset[ResourceKey]
    traits: frozenset[str]


@dataclass(frozen=True, slots=True)
class Snapshot:
    citation_id: str
    citable_at: datetime
    known_at: datetime
    system_id: str
    copy_location: str
    collection: str
    resource_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class Link:
    resource: ResourceKey
    basis: LinkBasis
    support: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Step:
    phase: Phase
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    verdict: Verdict
    steps: tuple[Step, ...]
