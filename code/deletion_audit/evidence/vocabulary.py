"""The one reviewable table of names: resource-id prefixes, field meanings and state words."""

from __future__ import annotations

import re
from collections.abc import Mapping

# Resource-id prefix -> contract system_id. Longer prefixes first so "audit_record_" wins.
RESOURCE_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("audit_record", "security_service"),
    ("data_flow", "integration_service"),
    ("email_delivery", "email_delivery_service"),
    ("legal_hold", "legal_service"),
    ("account", "account_service"),
    ("search", "search_service"),
    ("analytics", "analytics_platform"),
    ("support", "support_platform"),
    ("export", "export_service"),
    ("backup", "backup_service"),
    ("tombstone", "identity_service"),
    ("marketing", "marketing_platform"),
    ("object", "object_storage"),
    ("cache", "cache_service"),
)
RESOURCE_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(prefix for prefix, _ in RESOURCE_SYSTEMS) + r")_\d+(?![A-Za-z0-9_])"
)

# Inventory and typed SQL tables -> system, used when a table has no rows to infer it from.
TABLE_SYSTEMS = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}
COPY_SUFFIXES = (
    ("_read_replica", "read_replica"),
    ("_failover_replica", "failover_replica"),
    ("_reporting_replica", "reporting_replica"),
    ("_replica_ap_south", "regional_replica_ap_south"),
    ("_replica_eu_west", "regional_replica_eu_west"),
    ("_primary", "primary"),
)

# Fields holding what someone looked up: they link a record to a Subject, nothing more.
LOOKUP_FIELDS = frozenset({
    "lookup_key", "match_key", "query", "correlation_key", "subject_key", "scan_key",
    "subject_ref", "lookup_value", "subject_selector",
})
# Fields holding who the record belongs to: a Stable Reference there keeps the record
# attributable, but these are keys rather than exposed content.
REFERENCE_FIELDS = frozenset({"subject_reference", "customer_reference", "user_id", "owner_key", "uploaded_by"})
# Labels printed on screenshots and PDFs for fields the structured exports name differently
# (evidence_58 prints "file" and "uploader" for an attachment's file_name and uploaded_by).
VISUAL_FIELD_ALIASES = {"file": "file_name", "filename": "file_name", "uploader": "uploaded_by"}
NAME_FIELDS = frozenset({"full_name", "name", "display_name", "requester_name"})
# Fields that describe what a record holds, even when empty. An entry with one of them (or a
# stated condition) says whether the record still exposes identifiers.
CONTENT_FIELDS = frozenset({
    "email", "phone", "email_hash", "full_name", "name", "display_name", "requester_name",
    "requester_email", "requester_phone", "owner_email", "owner_phone", "customer_email",
    "message_text", "message_excerpt", "user_id", "customer_reference", "subject_reference",
    "matched_value", "direct_identifiers",
})

PRESENT_RESULTS = frozenset({"FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "DISABLED", "BLOCKED"})
CONDITIONS = PRESENT_RESULTS | {"NOT_FOUND"}

# Read-only check activities and the state their wording confirms (ADR-0002).
CHECK_PHRASES = {
    "CHECK_DATA_FLOW": ("remains enabled", "flow_enabled"),
    "CHECK_RESTORE_ACCESS": ("can still be restored", "restore_allowed"),
}


class UnknownResourceError(ValueError):
    pass


def system_for(resource_id: str) -> str:
    for prefix, system in RESOURCE_SYSTEMS:
        if resource_id.startswith(prefix + "_") and resource_id[len(prefix) + 1:].isdigit():
            return system
    raise UnknownResourceError(f"no system known for resource id {resource_id!r}")


def copy_location(table: str) -> str:
    for suffix, location in COPY_SUFFIXES:
        if table.endswith(suffix):
            return location
    return "primary"


def table_base(table: str) -> str:
    for suffix, _ in COPY_SUFFIXES:
        if table.endswith(suffix):
            return table[: -len(suffix)]
    return table


def presence(fields: Mapping[str, object], is_record: bool) -> bool | None:
    """Whether the entry shows the resource existing (True), missing (False) or neither (None)."""
    result = str(fields.get("result", "")).upper()
    if result == "NOT_FOUND" or result == "MISS":
        return False
    if result in PRESENT_RESULTS or result == "HIT":
        return True
    status = str(fields.get("http_status", fields.get("status_code", "")))
    if status == "404":
        return False
    if status == "200":
        return True
    if "rows" in fields:
        return _as_int(fields["rows"]) > 0
    if "total" in fields and "query" in fields:
        return _as_int(fields["total"]) > 0
    if "archive_present" in fields:
        return _as_bool(fields["archive_present"])
    if "available" in fields:
        return _as_bool(fields["available"])
    if is_record:
        return True
    # A log event that read, scanned or evaluated the resource observed it existing; a bare
    # "..._started" line only says someone asked.
    event = str(fields.get("event", ""))
    return True if event and not event.endswith("_started") else None


def condition(fields: Mapping[str, object]) -> str | None:
    """What the entry says about identifiers in the record, when it says anything."""
    result = str(fields.get("result", "")).upper()
    if result in CONDITIONS:
        return result
    if "direct_identifiers" in fields:
        return "IDENTIFIABLE" if _as_int(fields["direct_identifiers"]) > 0 else "ANONYMIZED"
    if fields.get("detector") == "direct_identifier" and "matched_value" in fields:
        return "IDENTIFIABLE"
    return None


def flow_enabled(fields: Mapping[str, object], resource_id: str) -> bool | None:
    if "enabled" in fields:
        return _as_bool(fields["enabled"])
    result = str(fields.get("result", "")).upper()
    # A connector console labels a running flow "Healthy" (evidence_14).
    if resource_id.startswith("data_flow_") and result in ("ACTIVE", "HEALTHY", "DISABLED"):
        return result != "DISABLED"
    return None


def restore_allowed(fields: Mapping[str, object], resource_id: str) -> bool | None:
    if "restore_allowed" in fields:
        return _as_bool(fields["restore_allowed"])
    if "deletion_block" in fields:
        return not _as_bool(fields["deletion_block"]) and _as_bool(fields.get("eligible", True))
    if "decision" in fields:
        return str(fields["decision"]).lower() == "allow"
    result = str(fields.get("result", "")).upper()
    if resource_id.startswith("backup_") and result in ("ACTIVE", "BLOCKED"):
        return result == "ACTIVE"
    return None


def _as_bool(value: object) -> bool:
    # SQL snapshots store flags as INTEGER 0/1 (e.g. restore_allowed), logs and JSON as true/false.
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0
