"""Deletion Intent from Request Text, one cached model answer per distinct text (FR-2)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from deletion_audit.llm import LlmClient, LlmRequest

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "intent_classification.md"
REQUEST_TYPES = (
    "ACCOUNT_DELETION", "DELETION_FOLLOW_UP", "DATA_ACCESS", "DATA_EXPORT", "CORRECTION",
    "MARKETING_OPT_OUT", "ACCOUNT_SUPPORT", "OTHER",
)
SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["DELETION", "NOT_APPLICABLE"]},
        "request_type": {"type": "string", "enum": list(REQUEST_TYPES)},
        "language": {"type": "string"},
    },
    "required": ["intent", "request_type", "language"],
}


@dataclass(frozen=True, slots=True)
class IntentDecision:
    has_deletion_intent: bool
    request_type: str
    language: str


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def classify_intents(texts: Iterable[str], llm: LlmClient, *, model: str) -> dict[str, IntentDecision]:
    """Decision for every given text; texts equal after normalization share one model call."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    by_normalized: dict[str, IntentDecision] = {}
    decisions = {}
    for text in texts:
        normalized = normalize_text(text)
        if normalized not in by_normalized:
            answer = llm.ask(LlmRequest(
                purpose="intent", model=model, effort="low", max_tokens=4096,
                prompt_id="intent_classification", system_prompt=prompt, text=normalized,
                attachment=None, json_schema=SCHEMA,
            ))
            by_normalized[normalized] = IntentDecision(
                answer.data["intent"] == "DELETION", answer.data["request_type"], answer.data["language"],
            )
        decisions[text] = by_normalized[normalized]
    return decisions
