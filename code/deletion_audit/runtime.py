"""Builds the model client for a run: cached answers first, live calls only for misses."""

from __future__ import annotations

from pathlib import Path

from deletion_audit.llm import LlmClient, Transport
from deletion_audit.settings import Settings


def make_client(settings: Settings, *, offline: bool, cache_dir: Path | None = None) -> LlmClient:
    """Cached answers are always replayed. Misses go to the provider when a Gemini key is
    configured and `offline` is off; otherwise a miss raises CacheMissError."""
    transport: Transport | None = None
    if not offline and settings.provider == "gemini" and settings.has_api_key:
        from deletion_audit.llm.gemini_transport import GeminiTransport

        transport = GeminiTransport(settings.api_key or "", settings.min_interval, settings.timeouts)
    return LlmClient(cache_dir or settings.cache_dir, transport)
