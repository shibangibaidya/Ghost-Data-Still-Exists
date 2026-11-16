"""Loads the participant dataset into immutable records. Read-only; never repairs data."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from deletion_audit.model import Case

T = TypeVar("T")

_CASE_COLUMNS = (
    "case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text",
)


class DatasetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IdentityRow:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityChange:
    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True, slots=True)
class Activity:
    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    file_id: str
    path: Path
    origin: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Dataset:
    root: Path
    cases: tuple[Case, ...]
    sample_cases: tuple[Case, ...]
    identities: tuple[IdentityRow, ...]
    identity_changes: tuple[IdentityChange, ...]
    activities: tuple[Activity, ...]
    rules: tuple[Rule, ...]
    manifest: tuple[ManifestEntry, ...]
    _citation_times: dict[str, datetime]

    def citation_time(self, citation_id: str) -> datetime | None:
        """Capture time of a file, or event time of an activity or identity change."""
        return self._citation_times.get(citation_id)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return parsed


def case_order(case_id: str) -> tuple[int, str]:
    prefix, _, number = case_id.rpartition("_")
    return (int(number), prefix) if number.isdigit() else (-1, case_id)


def read_cases(path: Path) -> tuple[Case, ...]:
    cases = _read_rows(
        path,
        _CASE_COLUMNS,
        lambda row: Case(
            case_id=row["case_id"],
            request_email=row["email"],
            requested_at=parse_time(row["requested_at"]),
            reported_status=row["reported_status"],
            reported_at=parse_time(row["reported_at"]),
            audit_time=parse_time(row["audit_at"]),
            request_text=row["request_text"],
        ),
        key=lambda case: case.case_id,
    )
    return tuple(sorted(cases, key=lambda case: case_order(case.case_id)))


def load_dataset(root: Path) -> Dataset:
    identities = _read_rows(
        root / "user_identities.csv",
        ("user_id", "name", "email", "phone", "email_hash", "updated_at"),
        lambda row: IdentityRow(
            row["user_id"], row["name"], row["email"], row["phone"], row["email_hash"],
            parse_time(row["updated_at"]),
        ),
        key=lambda item: item.user_id,
    )
    changes = _read_rows(
        root / "user_audit_log.csv",
        ("audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"),
        lambda row: IdentityChange(
            row["audit_id"], row["user_id"], parse_time(row["event_time"]), row["field_name"],
            row["old_value"], row["new_value"],
        ),
        key=lambda item: item.audit_id,
    )
    activities = _read_rows(
        root / "system_activity.csv",
        ("activity_id", "event_time", "event_type", "source_system", "target_system",
         "resource_id", "reported_result", "details"),
        lambda row: Activity(
            row["activity_id"], parse_time(row["event_time"]), row["event_type"],
            row["source_system"], row["target_system"], row["resource_id"],
            row["reported_result"], row["details"],
        ),
        key=lambda item: item.activity_id,
    )
    rules = _read_rows(
        root / "deletion_rules.csv",
        ("rule_id", "data_category", "condition", "required_action"),
        lambda row: Rule(row["rule_id"], row["data_category"], row["condition"],
                         row["required_action"]),
        key=lambda item: item.rule_id,
    )
    manifest = _read_rows(
        root / "evidence_files.csv",
        ("file_id", "file_path", "origin", "captured_at"),
        lambda row: ManifestEntry(
            row["file_id"], root / row["file_path"], row["origin"], parse_time(row["captured_at"]),
        ),
        key=lambda item: item.file_id,
    )
    # A listed file that is not there would silently drop its evidence and could certify a
    # deletion that the file disproves, so the whole run stops and names every missing file.
    missing = [f"{entry.file_id} ({entry.path.relative_to(root).as_posix()})"
               for entry in manifest if not entry.path.is_file()]
    if missing:
        raise DatasetError(f"evidence_files.csv: {len(missing)} listed file(s) not found: {', '.join(missing)}")
    citation_times = {entry.file_id: entry.captured_at for entry in manifest}
    citation_times |= {item.activity_id: item.event_time for item in activities}
    citation_times |= {item.audit_id: item.event_time for item in changes}
    return Dataset(
        root=root,
        cases=read_cases(root / "deletion_requests.csv"),
        sample_cases=read_cases(root / "sample_deletion_requests.csv"),
        identities=tuple(identities),
        identity_changes=tuple(changes),
        activities=tuple(activities),
        rules=tuple(rules),
        manifest=tuple(manifest),
        _citation_times=citation_times,
    )


def _read_rows(
    path: Path,
    columns: tuple[str, ...],
    build: Callable[[dict[str, str]], T],
    key: Callable[[T], str],
) -> list[T]:
    items: list[T] = []
    seen: set[str] = set()
    for line_number, row in _csv_rows(path, columns):
        try:
            item = build(row)
        except (ValueError, TypeError) as error:
            raise DatasetError(f"{path.name}: row {line_number}: {error}") from error
        item_key = key(item)
        if item_key in seen:
            raise DatasetError(f"{path.name}: row {line_number}: duplicate id {item_key!r}")
        seen.add(item_key)
        items.append(item)
    return items


def _csv_rows(path: Path, columns: tuple[str, ...]) -> Iterator[tuple[int, dict[str, str]]]:
    if not path.is_file():
        raise DatasetError(f"{path.name}: file not found ({path})")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in columns if column not in (reader.fieldnames or ())]
        if missing:
            raise DatasetError(f"{path.name}: missing columns {missing}")
        for row in reader:
            yield reader.line_num, row
