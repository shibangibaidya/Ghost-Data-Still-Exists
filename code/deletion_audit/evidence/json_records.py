"""JSON evidence: any object with a resource_id is an entry; context is inherited from parents."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from deletion_audit.dataset import ManifestEntry, parse_time
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.evidence.entries import Entry
from deletion_audit.model import Snapshot

_TIME_KEYS = ("observed_at", "recorded_at", "captured_at")
_SYSTEMS = {system for _, system in vocab.RESOURCE_SYSTEMS}


class _Walker:
    def __init__(self, manifest: ManifestEntry) -> None:
        self.manifest = manifest
        self.entries: list[Entry] = []
        self.snapshots: list[Snapshot] = []

    def walk(self, node: object, known: datetime) -> None:
        if isinstance(node, list):
            for item in node:
                self.walk(item, known)
            return
        if not isinstance(node, dict):
            return
        known = _own_time(node, known)
        if isinstance(node.get("records"), list) and isinstance(node.get("collection"), str):
            self._snapshot(node, known)
        if "resource_id" in node:
            self._entry(node, known)
            return
        for value in node.values():
            if isinstance(value, dict | list):
                self.walk(value, known)

    def _entry(self, node: Mapping[str, object], known: datetime) -> None:
        fields, children = _flatten(node)
        self.entries.append(Entry(self.manifest.file_id, self.manifest.captured_at, known,
                                  len(self.entries), "primary", str(node["resource_id"]), fields,
                                  is_record=True))
        for child in children:
            self._entry(child, _own_time(child, known))

    def _snapshot(self, node: Mapping[str, object], known: datetime) -> None:
        records = [record for record in node["records"] if isinstance(record, dict)]
        if any("system" in record for record in records):
            return  # a ledger of lookups across systems, not a listing of one collection
        ids = frozenset(str(record["resource_id"]) for record in records if "resource_id" in record)
        systems = {vocab.system_for(resource_id) for resource_id in ids}
        store = node.get("data_store")
        if store in _SYSTEMS:
            systems.add(str(store))
        if len(systems) == 1:
            self.snapshots.append(Snapshot(self.manifest.file_id, self.manifest.captured_at, known,
                                           systems.pop(), "primary", str(node["collection"]), ids))


def parse_json(manifest: ManifestEntry) -> tuple[list[Entry], list[Snapshot]]:
    walker = _Walker(manifest)
    walker.walk(json.loads(manifest.path.read_text(encoding="utf-8")), manifest.captured_at)
    return walker.entries, walker.snapshots


def _own_time(node: Mapping[str, object], inherited: datetime) -> datetime:
    for key in _TIME_KEYS:
        value = node.get(key)
        if isinstance(value, str):
            return parse_time(value)
    return inherited


def _flatten(node: Mapping[str, object]) -> tuple[dict[str, object], list[Mapping[str, object]]]:
    """Scalar fields of an entry, with nested plain objects merged in; nested resources split out."""
    fields: dict[str, object] = {}
    children: list[Mapping[str, object]] = []
    for key, value in node.items():
        items = value if isinstance(value, list) else [value]
        scalars = []
        for item in items:
            if isinstance(item, dict):
                if "resource_id" in item:
                    children.append(item)
                else:
                    nested, grandchildren = _flatten(item)
                    for nested_key, nested_value in nested.items():
                        fields.setdefault(nested_key, nested_value)
                    children += grandchildren
            else:
                scalars.append(item)
        if scalars:
            fields[key] = scalars[0] if not isinstance(value, list) else " ".join(map(str, scalars))
    return fields, children
