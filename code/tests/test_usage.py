import pytest

from deletion_audit.llm import Usage, UsageEvent
from deletion_audit.usage import Price, render_usage_report, summarize_usage

PRICES = {
    "light": Price("Provider A", 1.00, 10.00, "test"),
    "heavy": Price("Provider A", 2.00, 20.00, "test"),
}


def event(key: str, model: str, *, cached: bool, tokens: tuple[int, int, int] = (100, 10, 0),
          requested: str | None = None, purpose: str = "intent") -> UsageEvent:
    return UsageEvent(purpose, requested or model, model, Usage(*tokens), cached, key)


def test_live_and_replayed_calls_are_counted_per_model_and_overall() -> None:
    events = [
        event("a", "light", cached=True),
        event("b", "light", cached=False),
        event("c", "heavy", cached=True, tokens=(1000, 100, 400), purpose="visual_evidence"),
    ]

    summary = summarize_usage(events, requests=4, prices=PRICES)

    light, heavy = sorted(summary.models, key=lambda m: m.model, reverse=True)
    assert (light.model, light.calls, light.live_calls, light.replayed_calls) == ("light", 2, 1, 1)
    assert (light.input_tokens, light.output_tokens, light.total_tokens) == (200, 20, 220)
    assert heavy.thinking_tokens == 400 and heavy.purposes == ("visual_evidence",)
    overall = summary.overall
    assert (overall.calls, overall.live_calls, overall.replayed_calls) == (3, 1, 2)
    assert overall.total_tokens == 220 + 1500


def test_cost_bills_thinking_at_the_output_rate() -> None:
    summary = summarize_usage([event("c", "heavy", cached=True, tokens=(1_000_000, 100_000, 400_000))],
                              requests=1, prices=PRICES)

    assert summary.models[0].cost_usd == pytest.approx(2.00 + 0.5 * 20.00)


def test_an_answer_used_twice_is_one_call_and_live_wins() -> None:
    events = [event("a", "light", cached=False), event("a", "light", cached=True)]

    summary = summarize_usage(events, requests=1, prices=PRICES)

    assert (summary.overall.calls, summary.overall.live_calls, summary.overall.input_tokens) == (1, 1, 100)


def test_cost_is_grouped_by_the_answering_model_and_unpriced_models_are_flagged() -> None:
    summary = summarize_usage([event("a", "alias-target", cached=True, requested="light")],
                              requests=1, prices=PRICES)

    usage = summary.models[0]
    assert (usage.model, usage.requested, usage.cost_usd) == ("alias-target", ("light",), None)
    assert summary.overall.cost_usd is None
    assert "not priced" in render_usage_report(summary, [], prices=PRICES)


def test_report_shows_totals_and_averages_and_is_deterministic() -> None:
    events = [event("a", "light", cached=True, tokens=(300, 30, 0)), event("b", "heavy", cached=True)]
    summary = summarize_usage(events, requests=2, prices=PRICES)

    report = render_usage_report(summary, [("Requests", "2 rows")], prices=PRICES)

    assert report == render_usage_report(summary, [("Requests", "2 rows")], prices=PRICES)
    assert "- Requests: 2 rows" in report
    assert "| **all models** | 400 | 40 | 0 | 440 | 220.0 |" in report
    assert "| light | 300 | 30 | 0 | 330 | 165.0 | 0.0006 |" in report
