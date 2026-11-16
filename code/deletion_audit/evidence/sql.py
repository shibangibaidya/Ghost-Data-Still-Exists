"""SQL snapshot exports: executed in an in-memory SQLite database, one entry per row."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime

from deletion_audit.dataset import ManifestEntry, parse_time
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.evidence.entries import Entry
from deletion_audit.model import Snapshot

_CAPTURED = re.compile(r"Captured at:\s*(\S+)")


def parse_sql(manifest: ManifestEntry) -> tuple[list[Entry], list[Snapshot]]:
    text = manifest.path.read_text(encoding="utf-8")
    header = _CAPTURED.search(text)
    captured = parse_time(header.group(1)) if header else manifest.captured_at
    database = sqlite3.connect(":memory:")
    try:
        database.executescript(text)
        tables = sorted(name for (name,) in database.execute(
            "select name from sqlite_master where type='table'"))
        scope = (
            {system: parse_time(at) for system, at in database.execute("select * from snapshot_scope")}
            if "snapshot_scope" in tables else None
        )
        entries: list[Entry] = []
        snapshots: list[Snapshot] = []
        for table in tables:
            if table == "snapshot_scope":
                continue
            cursor = database.execute(f'select * from "{table}"')
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor]
            table_entries, table_snapshots = _table(manifest, table, rows, captured, scope, len(entries))
            entries += table_entries
            snapshots += table_snapshots
        return entries, snapshots
    finally:
        database.close()


def _table(
    manifest: ManifestEntry,
    table: str,
    rows: list[dict[str, object]],
    captured: datetime,
    scope: dict[str, datetime] | None,
    first_ordinal: int,
) -> tuple[list[Entry], list[Snapshot]]:
    location = vocab.copy_location(table)
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    entries = []
    for offset, row in enumerate(rows):
        fields = dict(row)
        if "record_json" in fields:
            fields.update(json.loads(str(fields.pop("record_json"))))
        resource_id = str(fields["resource_id"])
        system = vocab.system_for(resource_id)
        collection = str(fields.get("collection_name") or vocab.table_base(table))
        groups[(system, collection)].add(resource_id)
        known = scope.get(system, captured) if scope else captured
        entries.append(Entry(manifest.file_id, manifest.captured_at, known, first_ordinal + offset,
                             location, resource_id, fields, is_record=True))
    if not rows and vocab.table_base(table) in vocab.TABLE_SYSTEMS:
        groups[(vocab.TABLE_SYSTEMS[vocab.table_base(table)], vocab.table_base(table))] = set()
    snapshots = [
        Snapshot(manifest.file_id, manifest.captured_at, scope.get(system, captured) if scope else captured,
                 system, location, collection, frozenset(ids))
        for (system, collection), ids in sorted(groups.items())
        if scope is None or system in scope
    ]
    return entries, snapshots
