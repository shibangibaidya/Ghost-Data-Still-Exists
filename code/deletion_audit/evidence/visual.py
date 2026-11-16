"""PNG and PDF evidence: transcribed once by the vision model (cached), then corrected by the
human-verified overlay in code/cache/visual_verification.json (FR-15, FR-15a)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from deletion_audit.dataset import Activity, ManifestEntry, parse_time
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.evidence.entries import Entry
from deletion_audit.llm import Attachment, LlmClient, LlmRequest

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "evidence_extraction.md"
MEDIA_TYPES = {".png": "image/png", ".pdf": "application/pdf"}
MAX_TOKENS = 32768
_NULLABLE_STRING = {"type": ["string", "null"]}
_CATALOG_ALIAS = re.compile(r"(?<![\w-])(\w+_\d+) \(([^()\s]+)\)")
_TOKEN_EDGES = ".,;:()[]\"'"

Aliases = Mapping[str, tuple[str, datetime]]  # exact business reference -> (resource_id, known from)
SCHEMA = {
    "type": "object",
    "properties": {
        "document_kind": {"type": "string"},
        "visible_identifiers": {"type": "array", "items": {"type": "string"}},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "system": _NULLABLE_STRING,
                    "record_type": _NULLABLE_STRING,
                    "status": _NULLABLE_STRING,
                    "observed_at": _NULLABLE_STRING,
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                            "required": ["name", "value"],
                        },
                    },
                },
                "required": ["resource_id", "system", "record_type", "status", "observed_at", "fields"],
            },
        },
    },
    "required": ["document_kind", "visible_identifiers", "records"],
}


class UnverifiedExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualReader:
    """Turns one visual Evidence Item into entries. Corrections are keyed by the file's sha256,
    so they survive prompt changes; raw model output in the cache is never edited."""

    llm: LlmClient
    model: str
    corrections_path: Path
    require_verified: bool

    def extract(self, manifest: ManifestEntry) -> dict:
        data = manifest.path.read_bytes()
        answer = self.llm.ask(LlmRequest(
            # Gemini counts thinking against max_tokens: HIGH thinking used up to 15,357 of 16,000
            # and ran out on evidence_04, so thinking is LOW and the budget doubled.
            purpose="visual_evidence", model=self.model, effort="low", max_tokens=MAX_TOKENS,
            prompt_id="evidence_extraction", system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            text=f"file_id: {manifest.file_id}\nfile name: {manifest.path.name}",
            attachment=Attachment(data, MEDIA_TYPES[manifest.path.suffix.lower()]), json_schema=SCHEMA,
        # A transcription is checked by hand (FR-15), so which model wrote it matters less than
        # not spending the small free quota on a file that is already answered (ADR-0004).
        ), any_model=True)
        verification = self._verification(hashlib.sha256(data).hexdigest())
        if verification is None and self.require_verified:
            raise UnverifiedExtractionError(f"{manifest.file_id}: visual extraction not verified yet")
        return apply_corrections(answer.data, (verification or {}).get("corrections", []))

    def entries(self, manifest: ManifestEntry, aliases: Aliases | None = None) -> list[Entry]:
        return to_entries(manifest, self.extract(manifest), aliases)

    def _verification(self, file_sha: str) -> Mapping | None:
        if not self.corrections_path.is_file():
            return None
        return json.loads(self.corrections_path.read_text(encoding="utf-8")).get(file_sha)


def apply_corrections(extraction: Mapping, corrections: list[Mapping]) -> dict:
    """Corrections: {"op": "set_field"|"set"|"add_record"|"remove_record", ...}."""
    result = json.loads(json.dumps(extraction))
    records = {record["resource_id"]: record for record in result["records"]}
    for correction in corrections:
        op, resource_id = correction["op"], correction.get("resource_id")
        if op in ("set", "set_field") and resource_id not in records:
            raise ValueError(f"correction targets {resource_id!r}, which the extraction does not contain")
        if op == "add_record":
            result["records"].append(correction["record"])
            records[correction["record"]["resource_id"]] = correction["record"]
        elif op == "remove_record":
            result["records"] = [r for r in result["records"] if r["resource_id"] != resource_id]
        elif op == "set":
            records[resource_id][correction["field"]] = correction["corrected"]
        elif op == "set_field":
            fields = [f for f in records[resource_id]["fields"] if f["name"] != correction["field"]]
            if correction["corrected"] is not None:
                fields.append({"name": correction["field"], "value": correction["corrected"]})
            records[resource_id]["fields"] = fields
        else:
            raise ValueError(f"unknown correction op {op!r}")
    return result


def catalog_aliases(activities: Iterable[Activity]) -> dict[str, tuple[str, datetime]]:
    """Business references the data catalog ties to a resource, e.g. "export_1842
    (NST-2026-0718-1842)". Matched exactly, and known only from the catalog activity on (SPEC A11)."""
    aliases: dict[str, tuple[str, datetime]] = {}
    for activity in sorted(activities, key=lambda a: (a.event_time, a.activity_id)):
        if activity.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        for resource_id, alias in _CATALOG_ALIAS.findall(activity.details):
            if alias != resource_id and vocab.RESOURCE_ID.fullmatch(resource_id):
                aliases.setdefault(alias, (resource_id, activity.event_time))
    return aliases


def to_entries(manifest: ManifestEntry, extraction: Mapping, aliases: Aliases | None = None) -> list[Entry]:
    entries = []
    for ordinal, record in enumerate(extraction["records"]):
        resource_id = record["resource_id"].strip()
        known_at = manifest.captured_at
        if not vocab.RESOURCE_ID.fullmatch(resource_id):
            alias = _alias_of(record, aliases or {})
            if alias is None:
                continue  # a business reference, not a resource in any system
            resource_id, catalogued_at = alias
            known_at = max(known_at, catalogued_at)
        fields: dict[str, object] = {_field_name(f["name"]): f["value"] for f in record["fields"]}
        fields["resource_id"] = resource_id
        if record.get("record_type"):
            fields["record_type"] = record["record_type"]
        if record.get("status"):
            status = _status(record["status"])
            # "DELETED" labels a deleted-account reference that still exists, not a missing record.
            fields["account_status" if status == "DELETED" else "result"] = status
        # A screenshot or PDF shows the record as it stood when captured; dates printed in it
        # ("received 18 Jun") are the record's history, so they must not move it out of an
        # Episode it was captured in (D9).
        entries.append(Entry(manifest.file_id, manifest.captured_at, known_at,
                             ordinal, "primary", resource_id, fields, is_record=True))
    return entries


def _alias_of(record: Mapping, aliases: Aliases) -> tuple[str, datetime] | None:
    """The catalogued resource a record is, by its own reference or a whole token in a field."""
    tokens = [record["resource_id"].strip()]
    for field in record["fields"]:
        tokens += [token.strip(_TOKEN_EDGES) for token in str(field["value"]).split()]
    return next((aliases[token] for token in tokens if token in aliases), None)


def _field_name(name: str) -> str:
    key = "_".join(name.strip().lower().replace("-", " ").split())
    return vocab.VISUAL_FIELD_ALIASES.get(key, key)


def _status(status: str) -> str:
    text = status.strip().upper().replace(" ", "_")
    return {"ENABLED": "ACTIVE", "RESTORE_ALLOWED": "ACTIVE"}.get(text, text)


def _time(value: object):
    try:
        return parse_time(str(value)) if value else None
    except ValueError:
        return None
