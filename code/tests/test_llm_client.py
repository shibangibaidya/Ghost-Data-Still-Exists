import json
from pathlib import Path

import pytest

from deletion_audit.llm import (
    Attachment,
    CacheMissError,
    LlmClient,
    LlmRequest,
    ModelResponseError,
    TransportReply,
    Usage,
)
from deletion_audit.llm.pacing import RetryableError, call_with_backoff

SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["DELETION", "NOT_APPLICABLE"]}},
    "required": ["intent"],
}


def request(text: str = "please delete my account", **overrides) -> LlmRequest:
    values = dict(purpose="intent", model="model-a", effort="low", max_tokens=512,
                  prompt_id="intent_classification", system_prompt="classify", text=text,
                  attachment=None, json_schema=SCHEMA)
    values.update(overrides)
    return LlmRequest(**values)


class FakeTransport:
    def __init__(self, *replies: TransportReply) -> None:
        self.replies = list(replies)
        self.calls: list[LlmRequest] = []

    def send(self, req: LlmRequest) -> TransportReply:
        self.calls.append(req)
        return self.replies.pop(0)


def reply(data, finish: str = "STOP") -> TransportReply:
    return TransportReply(data=data, usage=Usage(10, 3, 5), served_model="model-a-001", finish_reason=finish)


def test_first_call_goes_live_and_the_second_is_served_from_cache(workdir: Path) -> None:
    transport = FakeTransport(reply({"intent": "DELETION"}))
    client = LlmClient(workdir, transport)

    first = client.ask(request())
    second = LlmClient(workdir, None).ask(request())

    assert first.data == second.data == {"intent": "DELETION"}
    assert (first.from_cache, second.from_cache) == (False, True)
    assert len(transport.calls) == 1
    assert second.usage == Usage(10, 3, 5) and second.served_model == "model-a-001"


def test_cache_entry_never_stores_the_input(workdir: Path) -> None:
    LlmClient(workdir, FakeTransport(reply({"intent": "DELETION"}))).ask(request(text="secret words"))

    (entry,) = workdir.glob("*.json")
    text = entry.read_text(encoding="utf-8")
    assert "secret words" not in text
    assert json.loads(text)["input_sha"]


def test_offline_miss_is_an_error(workdir: Path) -> None:
    with pytest.raises(CacheMissError):
        LlmClient(workdir, None).ask(request())


def test_schema_mismatch_is_retried_once_then_raised(workdir: Path) -> None:
    good = LlmClient(workdir, FakeTransport(reply({"intent": "MAYBE"}), reply({"intent": "DELETION"})))
    assert good.ask(request()).data == {"intent": "DELETION"}

    bad = LlmClient(workdir, FakeTransport(reply({"wrong": 1}), reply({"intent": "MAYBE"})))
    with pytest.raises(ModelResponseError):
        bad.ask(request(text="another text"))
    assert not any("another" in p.read_text(encoding="utf-8") for p in workdir.glob("*.json"))


def test_unfinished_or_refused_responses_are_not_cached(workdir: Path) -> None:
    client = LlmClient(workdir, FakeTransport(reply({"intent": "DELETION"}, "MAX_TOKENS"),
                                              reply({"intent": "DELETION"}, "SAFETY")))
    with pytest.raises(ModelResponseError):
        client.ask(request())
    assert list(workdir.glob("*.json")) == []


def test_key_changes_with_model_prompt_and_attachment(workdir: Path) -> None:
    transport = FakeTransport(*(reply({"intent": "DELETION"}) for _ in range(3)))
    client = LlmClient(workdir, transport)

    client.ask(request())
    client.ask(request(model="model-b"))
    client.ask(request(attachment=Attachment(b"png-bytes", "image/png")))

    assert len(transport.calls) == 3
    assert len(list(workdir.glob("*.json"))) == 3


def test_usage_events_record_live_and_cached_calls(workdir: Path) -> None:
    client = LlmClient(workdir, FakeTransport(reply({"intent": "DELETION"})))
    client.ask(request())
    client.ask(request())

    assert [event.from_cache for event in client.usage_events] == [False, True]
    assert all(event.usage == Usage(10, 3, 5) for event in client.usage_events)


def test_backoff_retries_with_growing_jittered_delays() -> None:
    sleeps: list[float] = []
    attempts = iter([RetryableError("429"), RetryableError("429"), "done"])

    def call():
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = call_with_backoff(call, sleep=sleeps.append, jitter=lambda: 0.5, base_delay=2.0, max_attempts=5)

    assert result == "done"
    assert sleeps == [2.0 + 0.5 * 2.0, 4.0 + 0.5 * 2.0]  # jitter is a fraction of base_delay


def test_backoff_gives_up_after_max_attempts() -> None:
    def call():
        raise RetryableError("429")

    with pytest.raises(RetryableError):
        call_with_backoff(call, sleep=lambda _: None, jitter=lambda: 0.0, base_delay=1.0, max_attempts=3)


def test_any_model_replays_an_answer_for_the_same_input_from_another_model(workdir: Path) -> None:
    LlmClient(workdir, FakeTransport(reply({"intent": "DELETION"}))).ask(request(model="old-model"))

    exact = LlmClient(workdir, None)
    with pytest.raises(CacheMissError):
        exact.ask(request(model="new-model"))
    client = LlmClient(workdir, None)
    answer = client.ask(request(model="new-model", max_tokens=9999), any_model=True)

    assert answer.data == {"intent": "DELETION"} and answer.from_cache
    assert client.usage_events[-1].requested_model == "old-model"


def test_any_model_still_asks_when_the_input_differs(workdir: Path) -> None:
    LlmClient(workdir, FakeTransport(reply({"intent": "DELETION"}))).ask(request(model="old-model"))
    transport = FakeTransport(reply({"intent": "NOT_APPLICABLE"}))

    answer = LlmClient(workdir, transport).ask(request(text="other text", model="new-model"), any_model=True)

    assert answer.data == {"intent": "NOT_APPLICABLE"} and len(transport.calls) == 1


def test_backoff_waits_at_least_the_providers_retry_after() -> None:
    sleeps: list[float] = []
    attempts = iter([RetryableError("429", retry_after=40.0), "done"])

    def call():
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert call_with_backoff(call, sleep=sleeps.append, jitter=lambda: 0.0, base_delay=2.0) == "done"
    assert sleeps == [40.0]
