"""Writes one JSON trace per case so every Verdict can be explained."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def write_traces(directory: Path, traces: Mapping[str, Mapping[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for case_id, trace in traces.items():
        text = json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        (directory / f"{case_id}.json").write_text(text + "\n", encoding="utf-8", newline="\n")
