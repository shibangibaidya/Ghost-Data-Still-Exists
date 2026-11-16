import json
from types import SimpleNamespace

import httpx
import pytest

from deletion_audit.llm import Attachment, LlmRequest, ModelTimeoutError, QuotaExhaustedError, Usage
from deletion_audit.llm.pacing import RetryableError
from deletion_audit.llm.gemini_transport import GeminiTransport

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
TIMEOUTS = {"intent": 60.0, "visual_evidence": 180.0}


def request(purpose: str = "visual_evidence") -> LlmRequest:
    attachment = Attachment(b"png", "image/png") if purpose == "visual_evidence" else None
    return LlmRequest(purpose=purpose, model="gemini-test", effort="high", max_tokens=100,
                      prompt_id="p", system_prompt="s", text="t", attachment=attachment,
                      json_schema=SCHEMA)


def response() -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
        text=json.dumps({"ok": True}),
        usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=3,
                                       thoughts_token_count=11),
        model_version="gemini-test-001",
    )


class FakeModels:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.configs = []

    def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def transport(*outcomes) -> tuple[GeminiTransport, FakeModels]:
    sender = GeminiTransport("test-key", min_interval=0.0, timeouts=TIMEOUTS)
    models = FakeModels(*outcomes)
    sender._client = SimpleNamespace(models=models)
    return sender, models


@pytest.mark.parametrize(("purpose", "milliseconds"), [("intent", 60_000), ("visual_evidence", 180_000)])
def test_each_call_carries_its_purpose_timeout(purpose: str, milliseconds: int) -> None:
    sender, models = transport(response())

    reply = sender.send(request(purpose))

    assert models.configs[0].http_options.timeout == milliseconds
    assert reply.data == {"ok": True} and reply.finish_reason == "STOP"
    assert reply.usage == Usage(7, 3, 11) and reply.served_model == "gemini-test-001"


def test_a_timed_out_call_is_retried_once() -> None:
    sender, models = transport(httpx.ReadTimeout("slow"), response())

    assert sender.send(request()).data == {"ok": True}
    assert len(models.configs) == 2


def test_a_second_timeout_raises_instead_of_waiting_longer() -> None:
    sender, models = transport(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))

    with pytest.raises(ModelTimeoutError, match="180s"):
        sender.send(request())
    assert len(models.configs) == 2


def api_error(code: int, details: list) -> Exception:
    from google.genai import errors

    body = {"error": {"code": code, "message": "You exceeded your current quota", "status": "X",
                      "details": details}}
    return errors.APIError(code, body)


DAILY = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
          "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
         {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41000s"}]


def test_daily_quota_fails_at_once_without_retrying() -> None:
    sender, models = transport(api_error(429, DAILY), response())

    with pytest.raises(QuotaExhaustedError, match="daily quota"):
        sender.send(request())
    assert len(models.configs) == 1


def test_a_long_retry_delay_alone_counts_as_daily() -> None:
    sender, models = transport(api_error(429, [{"retryDelay": "7200s"}]), response())

    with pytest.raises(QuotaExhaustedError, match="2.0 h"):
        sender.send(request())


def test_per_minute_429_gives_up_after_three_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import functools

    import deletion_audit.llm.gemini_transport as gemini
    from deletion_audit.llm.pacing import call_with_backoff

    monkeypatch.setattr(gemini, "call_with_backoff",
                        functools.partial(call_with_backoff, sleep=lambda seconds: None))
    sender, models = transport(*(api_error(429, [{"retryDelay": "1s"}]) for _ in range(5)))

    with pytest.raises(RetryableError):
        sender.send(request())
    assert len(models.configs) == 4
