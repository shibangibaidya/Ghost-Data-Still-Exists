"""Log and text evidence. Lines of one trace merge into one entry, so a lookup key on the
"started" line attaches to the resource on the "completed" line."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

from deletion_audit.dataset import ManifestEntry, parse_time
from deletion_audit.evidence.entries import Entry

_PAIR = re.compile(r'(\w+)=("((?:[^"\\]|\\.)*)"|\S+)')
_BRACKETED = re.compile(r"\[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] (\w+): ?(.*)")
_TRACE_KEYS = ("trace_id", "requestId")


def parse_log(manifest: ManifestEntry) -> list[Entry]:
    lines = []
    for number, line in enumerate(manifest.path.read_text(encoding="utf-8").splitlines()):
        stamp, _, rest = line.partition(" ")
        try:
            moment = parse_time(stamp)
        except ValueError:
            continue
        lines.append((number, moment, _fields(rest.strip())))

    groups: dict[str, list[tuple[int, datetime, dict[str, object]]]] = defaultdict(list)
    for number, moment, fields in lines:
        trace = next((str(fields[key]) for key in _TRACE_KEYS if key in fields), None)
        groups[trace or f"line-{number}"].append((number, moment, fields))

    entries = []
    for members in groups.values():
        merged: dict[str, object] = {}
        for _, _, fields in members:
            # A cache line's result ("miss") is about the cache, not the resource the trace reads.
            merged.update({k: v for k, v in fields.items() if not ("cache" in fields and k == "result")})
        resources = sorted({str(fields["resource_id"]) for _, _, fields in members if "resource_id" in fields})
        known = max(moment for _, moment, _ in members)
        for resource_id in resources:
            entries.append(Entry(manifest.file_id, manifest.captured_at, known, members[0][0],
                                 "primary", resource_id, {**merged, "resource_id": resource_id},
                                 is_record=False))
    return sorted(entries, key=lambda entry: entry.ordinal)


def parse_text(manifest: ManifestEntry) -> list[Entry]:
    """Pipe rows: `- time | system | resource_id | record_type | result | identifier`."""
    entries = []
    for number, line in enumerate(manifest.path.read_text(encoding="utf-8").splitlines()):
        parts = [part.strip() for part in line.removeprefix("- ").split("|")]
        if not line.startswith("- ") or len(parts) != 6:
            continue
        stamp, system, resource_id, record_type, result, identifier = parts
        fields = {"system": system, "resource_id": resource_id, "record_type": record_type,
                  "result": result, "subject_reference": identifier}
        entries.append(Entry(manifest.file_id, manifest.captured_at, parse_time(stamp), number,
                             "primary", resource_id, fields, is_record=False))
    return entries


def _fields(rest: str) -> dict[str, object]:
    if rest.startswith("{"):
        return {key: value for key, value in json.loads(rest).items()}
    if rest.startswith("<"):  # syslog: <pri>version host service trace - - key=value...
        parts = rest.split(" ", 6)
        fields = _pairs(parts[6] if len(parts) > 6 else "")
        fields.setdefault("service", parts[2])
        fields.setdefault("trace_id", parts[3])
        return fields
    bracketed = _BRACKETED.match(rest)
    if bracketed:
        level, service, trace, event, tail = bracketed.groups()
        return {"level": level, "service": service, "trace_id": trace, "event": event, **_pairs(tail)}
    if rest.startswith("|"):
        parts = [part.strip() for part in rest.split("|")]
        level, service, trace, event, tail = (parts + [""] * 6)[1:6]
        return {"level": level, "service": service, "trace_id": trace, "event": event, **_pairs(tail)}
    fields = _pairs(rest)
    message = fields.pop("message", None)
    if isinstance(message, str):
        fields.update(_pairs(message))
    return fields


def _pairs(text: str) -> dict[str, object]:
    pairs: dict[str, object] = {}
    for key, raw, quoted in _PAIR.findall(text):
        pairs[key] = quoted.replace('\\"', '"').replace("\\\\", "\\") if raw.startswith('"') else raw
    return pairs
