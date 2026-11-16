"""Gemini adapter for the Transport port. The SDK is imported here only, so cached and offline
runs work without it."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping

from deletion_audit.llm import (
    LlmRequest,
    ModelTimeoutError,
    QuotaExhaustedError,
    TransportReply,
    Usage,
)
from deletion_audit.llm.pacing import Pacer, RetryableError, call_with_backoff

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4  # the call plus at most 3 retries for 429 and 5xx
# A suggested wait longer than this means a daily quota, not a per-minute one.
_LONGEST_WAIT = 600.0
# A timed-out call is retried once: a second timeout means the model is not answering, and
# backing off further would only stall the run.
_TIMEOUT_ATTEMPTS = 2


class _CallTimedOut(Exception):
    pass


def quota_hints(details: object) -> tuple[float | None, bool]:
    """(suggested wait in seconds, whether a per-day quota was hit) from a Gemini error body:
    google.rpc.RetryInfo.retryDelay ("43s") and QuotaFailure.violations[].quotaId."""
    error = details.get("error", details) if isinstance(details, dict) else {}
    retry_after, per_day = None, False
    for item in error.get("details", ()) if isinstance(error, dict) else ():
        if not isinstance(item, dict):
            continue
        delay = item.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                retry_after = float(delay[:-1])
            except ValueError:
                pass
        for violation in item.get("violations", ()):
            if "perday" in str(violation.get("quotaId", "")).lower():
                per_day = True
    return retry_after, per_day


class GeminiTransport:
    def __init__(self, api_key: str, min_interval: float, timeouts: Mapping[str, float]) -> None:
        from google import genai  # lazy: only live runs need the SDK

        # SDK retries stay off (the default without retry_options): pacing, backoff and the
        # timeout retry below own every repeat, so nothing waits silently.
        self._client = genai.Client(api_key=api_key)
        self._pacer = Pacer(min_interval)
        self._timeouts = dict(timeouts)

    def send(self, request: LlmRequest) -> TransportReply:
        import httpx
        from google.genai import errors, types

        timeout = self._timeouts[request.purpose]
        contents: list[object] = []
        if request.attachment is not None:
            contents.append(types.Part.from_bytes(data=request.attachment.data,
                                                  mime_type=request.attachment.media_type))
        if request.text is not None:
            contents.append(request.text)
        config = types.GenerateContentConfig(
            system_instruction=request.system_prompt,
            response_mime_type="application/json",
            response_json_schema=dict(request.json_schema),
            thinking_config=types.ThinkingConfig(thinking_level=request.effort.upper()),
            max_output_tokens=request.max_tokens,
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),  # milliseconds
        )

        def call():
            self._pacer.wait()
            try:
                return self._client.models.generate_content(model=request.model, contents=contents,
                                                            config=config)
            except errors.APIError as error:
                if error.code == 429:
                    retry_after, per_day = quota_hints(error.details)
                    if per_day or (retry_after or 0.0) > _LONGEST_WAIT:
                        wait = f", retry in {retry_after / 3600:.1f} h" if retry_after else ""
                        raise QuotaExhaustedError(
                            f"{request.model}: daily quota used up{wait}: {error.message}") from error
                    raise RetryableError(f"HTTP 429: {error.message}", retry_after) from error
                if error.code in _RETRYABLE_CODES:
                    raise RetryableError(f"HTTP {error.code}: {error.message}") from error
                raise
            except httpx.TimeoutException as error:
                raise _CallTimedOut() from error

        def report(error: RetryableError, delay: float) -> None:
            print(f"[{request.purpose}] {str(error)[:200]} - retrying in {delay:.0f}s", file=sys.stderr)

        for attempt in range(1, _TIMEOUT_ATTEMPTS + 1):
            try:
                response = call_with_backoff(call, on_retry=report, max_attempts=_MAX_ATTEMPTS)
                break
            except _CallTimedOut:
                print(f"[{request.purpose}] no answer within {timeout:.0f}s "
                      f"(attempt {attempt} of {_TIMEOUT_ATTEMPTS})", file=sys.stderr)
        else:
            raise ModelTimeoutError(f"{request.purpose} {request.prompt_id}: no answer within "
                                    f"{timeout:.0f}s after {_TIMEOUT_ATTEMPTS} attempts")

        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else "NONE"
        try:
            data = json.loads(response.text) if response.text else None
        except json.JSONDecodeError:
            data, finish = None, f"{finish}+INVALID_JSON"
        usage = response.usage_metadata
        return TransportReply(
            data=data,
            usage=Usage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
                thinking_tokens=(usage.thoughts_token_count or 0) if usage else 0,
            ),
            served_model=response.model_version or request.model,
            finish_reason=finish,
        )
