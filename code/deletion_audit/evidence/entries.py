"""Format-independent evidence entries and the token extractor shared by every parser."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from deletion_audit.dataset import parse_time
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.identity import normalize_name, normalize_phone
from deletion_audit.model import Observation, ResourceKey, SourceKind

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_USER = re.compile(r"(?<![A-Za-z0-9_])user_\d+(?![A-Za-z0-9_])")
_HASH = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_RETENTION_FIELDS = ("retention_until", "retained_until")
_TRAIT_VALUE_FIELDS = ("event", "collection", "collection_name", "source", "storage_class", "mode", "index_name")


@dataclass(frozen=True, slots=True)
class Entry:
    citation_id: str
    citable_at: datetime
    known_at: datetime
    ordinal: int
    copy_location: str
    resource_id: str
    fields: Mapping[str, object]
    is_record: bool


def extract_tokens(text: str) -> set[str]:
    tokens = {f"email:{match.lower().rstrip('.')}" for match in _EMAIL.findall(text)}
    tokens |= {f"user:{match}" for match in _USER.findall(text)}
    tokens |= {f"hash:{match.lower()}" for match in _HASH.findall(text)}
    return tokens


def to_observation(entry: Entry) -> Observation:
    fields = {key: value for key, value in entry.fields.items() if value not in (None, "", "null")}
    identifiers: set[str] = set()
    exposed: set[str] = set()
    references: set[str] = set()
    lineage: set[ResourceKey] = set()
    for key, value in fields.items():
        text = str(value)
        tokens = extract_tokens(text)
        if "phone" in key and len(normalize_phone(text)) >= 7:
            tokens.add(f"phone:{normalize_phone(text)}")
        if key in vocab.NAME_FIELDS:
            tokens.add(f"name:{normalize_name(text)}")
        identifiers |= tokens
        if key not in vocab.LOOKUP_FIELDS:
            references |= {token for token in tokens if token.startswith("user:")}
            if key not in vocab.REFERENCE_FIELDS:
                exposed |= {token for token in tokens if not token.startswith("user:")}
        for other in vocab.RESOURCE_ID.findall(text):
            if other != entry.resource_id:
                lineage.add(ResourceKey(vocab.system_for(other), other))
    retained = next((fields[name] for name in _RETENTION_FIELDS if name in fields), None)
    return Observation(
        citation_id=entry.citation_id,
        source_kind=SourceKind.EVIDENCE_ITEM,
        citable_at=entry.citable_at,
        known_at=entry.known_at,
        ordinal=entry.ordinal,
        explicit=True,
        resource=ResourceKey(vocab.system_for(entry.resource_id), entry.resource_id),
        copy_location=entry.copy_location,
        present=vocab.presence(fields, entry.is_record),
        identifiers=frozenset(identifiers),
        exposed=frozenset(exposed),
        references=frozenset(references),
        stated_category=str(fields["record_type"]) if "record_type" in fields else None,
        stated_condition=vocab.condition(fields),
        flow_enabled=vocab.flow_enabled(fields, entry.resource_id),
        restore_allowed=vocab.restore_allowed(fields, entry.resource_id),
        retained_until=_time_or_none(retained),
        lineage=frozenset(lineage),
        traits=frozenset(_traits(entry.fields)),
    )


def _traits(fields: Mapping[str, object]) -> set[str]:
    """Record-shape hints. Field names count even when the value is empty: an emptied email
    field is evidence of redaction, a missing one is not."""
    traits = {f"field:{key}" for key in fields}
    fields = {key: value for key, value in fields.items() if value not in (None, "", "null")}
    traits |= {f"{name}:{fields[name]}" for name in _TRAIT_VALUE_FIELDS if name in fields}
    if "account_status" in fields:
        traits.add(f"status:{fields['account_status']}")
    return traits


def _time_or_none(value: object) -> datetime | None:
    try:
        return parse_time(str(value)) if value is not None else None
    except ValueError:
        return None
