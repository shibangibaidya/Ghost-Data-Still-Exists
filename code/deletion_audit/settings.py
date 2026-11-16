"""Runtime settings from the repo-root `.env` file and the process environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER_VARIABLE = "DELETION_AUDIT_PROVIDER"
# The shipped cache was built with Gemini, so a run with no .env replays it (cache keys include
# the model). Without a key, a cache miss still fails instead of calling anything.
DEFAULT_PROVIDER = "gemini"

# Default models per provider. A provider without defaults must name its models explicitly.
_DEFAULT_MODELS: dict[str, tuple[str | None, str | None]] = {
    "anthropic": ("claude-haiku-5-5", "claude-opus-5-5"),
    # Free-tier probes on 2026-10-25: 3.8-flash hung and 3.5-flash-lite answered in 6 s, so the
    # light model takes intent. 3.7-flash is billed against the 3.8-flash free quota (20 requests
    # a day), which ran out after 5 of the 15 visuals, so 3.5-flash takes the rest (ADR-0004).
    "gemini": ("gemini-3.5-flash-lite", "gemini-3.5-flash"),
}
DEFAULT_MIN_INTERVAL = 4.0  # seconds between live calls; 429s additionally back off
# Seconds before a live call is abandoned. Visual calls took 88 s in the probe, so 180 s leaves
# room for a slow answer without letting a hung connection stall the run.
DEFAULT_INTENT_TIMEOUT = 60.0
DEFAULT_VISUAL_TIMEOUT = 180.0
_KEY_VARIABLES = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


@dataclass(frozen=True, slots=True)
class Settings:
    repo_root: Path
    provider: str | None
    intent_model: str | None
    visual_model: str | None
    min_interval: float = DEFAULT_MIN_INTERVAL
    intent_timeout: float = DEFAULT_INTENT_TIMEOUT
    visual_timeout: float = DEFAULT_VISUAL_TIMEOUT
    api_key: str | None = field(default=None, repr=False)

    @property
    def timeouts(self) -> dict[str, float]:
        """Per-purpose live-call timeout in seconds, keyed like `LlmRequest.purpose`."""
        return {"intent": self.intent_timeout, "visual_evidence": self.visual_timeout}

    @property
    def dataset_dir(self) -> Path:
        return self.repo_root / "dataset"

    @property
    def output_path(self) -> Path:
        return self.repo_root / "output.csv"

    @property
    def cache_dir(self) -> Path:
        return self.repo_root / "code" / "cache" / "llm"

    @property
    def trace_dir(self) -> Path:
        return self.repo_root / ".audit_traces"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_environment(
        cls, repo_root: Path, environ: Mapping[str, str] | None = None
    ) -> Settings:
        values = _read_dotenv(repo_root / ".env")
        values.update(os.environ if environ is None else environ)

        provider = values.get(PROVIDER_VARIABLE) or DEFAULT_PROVIDER
        if provider not in _DEFAULT_MODELS:
            raise ValueError(
                f"{PROVIDER_VARIABLE} must be one of {sorted(_DEFAULT_MODELS)}, got {provider!r}"
            )
        intent_default, visual_default = _DEFAULT_MODELS[provider]
        return cls(
            repo_root=repo_root,
            provider=provider,
            intent_model=values.get("DELETION_AUDIT_INTENT_MODEL") or intent_default,
            visual_model=values.get("DELETION_AUDIT_VISUAL_MODEL") or visual_default,
            min_interval=float(values.get("DELETION_AUDIT_MIN_INTERVAL") or DEFAULT_MIN_INTERVAL),
            intent_timeout=float(values.get("DELETION_AUDIT_INTENT_TIMEOUT") or DEFAULT_INTENT_TIMEOUT),
            visual_timeout=float(values.get("DELETION_AUDIT_VISUAL_TIMEOUT") or DEFAULT_VISUAL_TIMEOUT),
            api_key=values.get(_KEY_VARIABLES[provider]),
        )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
