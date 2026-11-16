"""Usage report for one run (FR-12, DESIGN.md M10): which model answers the run used, whether they
were live calls or cache replays, their tokens and an estimated cost."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from deletion_audit.llm import UsageEvent


@dataclass(frozen=True, slots=True)
class Price:
    provider: str
    input_per_million: float  # USD
    output_per_million: float  # USD; Gemini bills thinking tokens at the output rate
    source: str


# Paid-tier Standard list prices, checked 2026-10-25. The run itself used the free tier.
PRICES_CHECKED = "2026-10-25"
PRICES: dict[str, Price] = {
    "gemini-3.5-flash-lite": Price("Google Gemini API", 0.30, 2.50, "ai.google.dev/gemini-api/docs/pricing"),
    "gemini-3.8-flash": Price("Google Gemini API", 0.75, 3.75,
                              "ai.google.dev/gemini-api/docs/pricing (rate through 2026-12-31)"),
    # Not on Google's page on the check date; the rates are from price aggregators.
    "gemini-3.7-flash": Price("Google Gemini API", 0.75, 3.75, "aggregators (anotherwrapper.com)"),
    "gemini-3.6-flash": Price("Google Gemini API", 0.75, 3.75, "aggregators (anotherwrapper.com)"),
    "gemini-3.5-flash": Price("Google Gemini API", 1.50, 9.00, "aggregators (getmaxim.ai, pricepertoken.com)"),
}


@dataclass(frozen=True, slots=True)
class ModelUsage:
    provider: str
    model: str  # the model that answered
    requested: tuple[str, ...]
    purposes: tuple[str, ...]
    live_calls: int
    replayed_calls: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float | None  # None when the model has no price

    @property
    def calls(self) -> int:
        return self.live_calls + self.replayed_calls

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thinking_tokens


@dataclass(frozen=True, slots=True)
class UsageSummary:
    requests: int
    models: tuple[ModelUsage, ...]

    @property
    def overall(self) -> ModelUsage:
        costs = [m.cost_usd for m in self.models]
        return ModelUsage(
            provider=", ".join(sorted({m.provider for m in self.models})),
            model="all models",
            requested=tuple(sorted({r for m in self.models for r in m.requested})),
            purposes=tuple(sorted({p for m in self.models for p in m.purposes})),
            live_calls=sum(m.live_calls for m in self.models),
            replayed_calls=sum(m.replayed_calls for m in self.models),
            input_tokens=sum(m.input_tokens for m in self.models),
            output_tokens=sum(m.output_tokens for m in self.models),
            thinking_tokens=sum(m.thinking_tokens for m in self.models),
            cost_usd=None if None in costs else sum(costs),
        )


def summarize_usage(events: Iterable[UsageEvent], requests: int,
                    prices: Mapping[str, Price] = PRICES) -> UsageSummary:
    """One count per distinct cached answer: an answer asked for twice in a run is one call.
    Replayed answers carry the tokens recorded when they were first asked live."""
    first: dict[str, UsageEvent] = {}
    for event in events:
        # A key answered live and then replayed in the same run counts once, as live.
        if event.key not in first or not event.from_cache:
            first[event.key] = event
    by_model: dict[str, list[UsageEvent]] = {}
    for event in first.values():
        by_model.setdefault(event.served_model, []).append(event)

    models = []
    for model, group in sorted(by_model.items()):
        price = prices.get(model)
        input_tokens = sum(e.usage.input_tokens for e in group)
        output_tokens = sum(e.usage.output_tokens for e in group)
        thinking_tokens = sum(e.usage.thinking_tokens for e in group)
        cost = None if price is None else (
            input_tokens * price.input_per_million
            + (output_tokens + thinking_tokens) * price.output_per_million
        ) / 1_000_000
        models.append(ModelUsage(
            provider=price.provider if price else _provider_from_name(model),
            model=model,
            requested=tuple(sorted({e.requested_model for e in group})),
            purposes=tuple(sorted({e.purpose for e in group})),
            live_calls=sum(not e.from_cache for e in group),
            replayed_calls=sum(e.from_cache for e in group),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cost_usd=cost,
        ))
    return UsageSummary(requests, tuple(models))


def render_usage_report(summary: UsageSummary, run_meta: Sequence[tuple[str, str]],
                        prices: Mapping[str, Price] = PRICES) -> str:
    """Markdown with no secrets and no wall-clock time, so a repeated run writes the same file."""
    rows = [*summary.models, summary.overall] if summary.models else []
    per = max(summary.requests, 1)
    lines = [
        "# Usage report",
        "",
        "Generated by `code/main.py` for the full-dataset run that wrote `output.csv`. Rerunning",
        "the same command rewrites this file. It never contains API keys or other secrets.",
        "",
        "## Run",
        "",
        *[f"- {name}: {value}" for name, value in run_meta],
        "",
        "## Model calls",
        "",
        "A call is one distinct model answer the run used. Each answer is asked live once and",
        "cached; later runs replay it, so a replayed call costs nothing again. Tokens and cost",
        "below are those of the original live calls behind the answers this run used.",
        "Not counted: cached answers this run did not use (development probes, custom-case",
        "texts) and live attempts that failed while the cache was built, which were not recorded.",
        "Cost uses the model that answered; the provider can serve a requested name as another.",
        "",
        "| Provider | Model (answered) | Requested as | Purpose | Calls | Live | Replayed |",
        "|---|---|---|---|---|---|---|",
        *[f"| {m.provider} | {_name(m)} | {', '.join(m.requested)} | {', '.join(m.purposes)} | "
          f"{m.calls} | {m.live_calls} | {m.replayed_calls} |" for m in rows],
        "",
        "## Tokens and estimated cost",
        "",
        f"Averages are per request over the {summary.requests} requests. Output excludes thinking;",
        "thinking is shown separately and billed at the output rate.",
        "",
        "| Model | Input | Output | Thinking | Total | Avg tokens / request | Est. cost (USD) "
        "| Avg cost / request (USD) |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {_name(m)} | {m.input_tokens:,} | {m.output_tokens:,} | {m.thinking_tokens:,} | "
          f"{m.total_tokens:,} | {m.total_tokens / per:,.1f} | {_usd(m.cost_usd)} | "
          f"{_usd(None if m.cost_usd is None else m.cost_usd / per, 6)} |" for m in rows],
        "",
        "## Prices",
        "",
        f"Paid-tier Standard list prices in USD per million tokens, checked {PRICES_CHECKED}. The",
        "answers were produced on the Gemini API free tier, so the amount actually billed was 0;",
        "the estimate is what the same calls would cost on the paid tier.",
        "",
        "| Model | Input | Output (incl. thinking) | Source |",
        "|---|---|---|---|",
        *[f"| {model} | {price.input_per_million:.2f} | {price.output_per_million:.2f} | {price.source} |"
          for model, price in sorted(prices.items())],
    ]
    return "\n".join(lines) + "\n"


def _name(usage: ModelUsage) -> str:
    return f"**{usage.model}**" if usage.model == "all models" else usage.model


def _usd(value: float | None, places: int = 4) -> str:
    return "not priced" if value is None else f"{value:.{places}f}"


def _provider_from_name(model: str) -> str:
    if model.startswith("gemini"):
        return "Google Gemini API"
    if model.startswith("claude"):
        return "Anthropic"
    return "unknown"
