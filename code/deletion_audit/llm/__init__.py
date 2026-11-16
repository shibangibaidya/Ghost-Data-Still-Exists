"""The only seam in the system (DESIGN.md M3): a provider-neutral request, a Transport port,
and a client that answers each distinct request once and replays it from the cache after."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

FINISHED = "STOP"


class CacheMissError(RuntimeError):
    """Offline (no transport) and the answer is not cached."""


class ModelResponseError(RuntimeError):
    """The model did not finish normally or broke the schema, even after one retry."""


class ModelTimeoutError(RuntimeError):
    """No answer within the per-call timeout, even after one retry."""


class QuotaExhaustedError(RuntimeError):
    """The provider's daily quota is used up; retrying before it resets only wastes time."""


@dataclass(frozen=True, slots=True)
class Attachment:
    data: bytes
    media_type: str  # "image/png" or "application/pdf"


@dataclass(frozen=True, slots=True)
class LlmRequest:
    purpose: Literal["intent", "visual_evidence"]
    model: str
    effort: Literal["low", "medium", "high"]
    max_tokens: int
    prompt_id: str
    system_prompt: str
    text: str | None
    attachment: Attachment | None
    json_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int


@dataclass(frozen=True, slots=True)
class TransportReply:
    data: object
    usage: Usage
    served_model: str
    finish_reason: str


class Transport(Protocol):
    def send(self, request: LlmRequest) -> TransportReply: ...


@dataclass(frozen=True, slots=True)
class LlmAnswer:
    data: object
    usage: Usage
    served_model: str
    from_cache: bool
    key: str


@dataclass(frozen=True, slots=True)
class UsageEvent:
    purpose: str
    requested_model: str
    served_model: str
    usage: Usage
    from_cache: bool
    key: str


class LlmClient:
    def __init__(self, cache_dir: Path, transport: Transport | None) -> None:
        self._cache_dir = cache_dir
        self._transport = transport
        self._events: list[UsageEvent] = []

    @property
    def usage_events(self) -> tuple[UsageEvent, ...]:
        return tuple(self._events)

    @property
    def is_offline(self) -> bool:
        return self._transport is None

    def ask(self, request: LlmRequest, *, any_model: bool = False) -> LlmAnswer:
        """With `any_model`, a cached answer to the same prompt, schema and input is replayed even
        if another model or token budget produced it; the exact key still wins."""
        hashes = _hashes(request)
        key = _sha(json.dumps({"model": request.model, "prompt_id": request.prompt_id, **hashes},
                              sort_keys=True))
        path = self._cache_dir / f"{key}.json"
        if not path.is_file() and any_model:
            path = self._same_input_entry(request, hashes) or path
        if path.is_file():
            entry = json.loads(path.read_text(encoding="utf-8"))
            answer = LlmAnswer(entry["response"], Usage(**entry["usage"]), entry["served_model"], True,
                               entry["key"])
            requested_model = entry["requested_model"]
        elif self._transport is None:
            raise CacheMissError(f"no cached {request.purpose} answer for {request.prompt_id} ({key[:12]})")
        else:
            reply = self._ask_live(request)
            self._store(path, request, hashes, key, reply)
            answer = LlmAnswer(reply.data, reply.usage, reply.served_model, False, key)
            requested_model = request.model
        self._events.append(UsageEvent(request.purpose, requested_model, answer.served_model,
                                       answer.usage, answer.from_cache, answer.key))
        return answer

    def _same_input_entry(self, request: LlmRequest, hashes: Mapping[str, str]) -> Path | None:
        wanted = {"purpose": request.purpose, "prompt_id": request.prompt_id,
                  **{name: hashes[name] for name in ("prompt_sha", "schema_sha", "input_sha")}}
        matches = []
        for candidate in sorted(self._cache_dir.glob("*.json")) if self._cache_dir.is_dir() else ():
            entry = json.loads(candidate.read_text(encoding="utf-8"))
            if all(entry.get(name) == value for name, value in wanted.items()):
                matches.append((entry["created_at"], entry["key"], candidate))
        # The oldest answer wins, so the choice is stable however many later re-asks exist.
        return min(matches)[2] if matches else None

    def _ask_live(self, request: LlmRequest) -> TransportReply:
        problem = ""
        for _ in range(2):  # one retry for an unfinished answer or a schema break
            reply = self._transport.send(request)
            if reply.finish_reason != FINISHED:
                # The token split tells a thinking-heavy answer from a runaway one.
                problem = (f"finish_reason={reply.finish_reason} (output={reply.usage.output_tokens}, "
                           f"thinking={reply.usage.thinking_tokens}, max_tokens={request.max_tokens})")
                continue
            problem = schema_problem(reply.data, request.json_schema)
            if not problem:
                return reply
        raise ModelResponseError(f"{request.purpose} {request.prompt_id}: {problem}")

    def _store(self, path: Path, request: LlmRequest, hashes: Mapping[str, str], key: str,
               reply: TransportReply) -> None:
        entry = {
            "key": key,
            "purpose": request.purpose,
            "requested_model": request.model,
            "served_model": reply.served_model,
            "prompt_id": request.prompt_id,
            **hashes,
            "response": reply.data,
            "usage": {"input_tokens": reply.usage.input_tokens, "output_tokens": reply.usage.output_tokens,
                      "thinking_tokens": reply.usage.thinking_tokens},
            "finish_reason": reply.finish_reason,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)


def schema_problem(data: object, schema: Mapping[str, object], where: str = "$") -> str:
    """The first way `data` breaks the JSON-schema subset our prompts use, or "" if none."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(data, dict):
            return f"{where} is not an object"
        for name in schema.get("required", ()):
            if name not in data:
                return f"{where}.{name} is missing"
        properties = schema.get("properties", {})
        for name, value in data.items():
            if name in properties:
                problem = schema_problem(value, properties[name], f"{where}.{name}")
                if problem:
                    return problem
        return ""
    if kind == "array":
        if not isinstance(data, list):
            return f"{where} is not an array"
        for position, item in enumerate(data):
            problem = schema_problem(item, schema.get("items", {}), f"{where}[{position}]")
            if problem:
                return problem
        return ""
    allowed = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}
    types = kind if isinstance(kind, list) else [kind] if kind else []
    if types and not any(data is None if t == "null" else isinstance(data, allowed.get(t, object)) for t in types):
        return f"{where} is not {kind}"
    if "enum" in schema and data not in schema["enum"]:
        return f"{where}={data!r} is not one of {schema['enum']}"
    return ""


def _hashes(request: LlmRequest) -> dict[str, str]:
    payload = (request.text or "").encode("utf-8")
    if request.attachment is not None:
        payload += b"\0" + request.attachment.media_type.encode() + b"\0" + request.attachment.data
    return {
        "prompt_sha": _sha(request.system_prompt),
        "schema_sha": _sha(json.dumps(request.json_schema, sort_keys=True)),
        "input_sha": hashlib.sha256(payload).hexdigest(),
        "settings_sha": _sha(f"{request.effort}|{request.max_tokens}"),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
