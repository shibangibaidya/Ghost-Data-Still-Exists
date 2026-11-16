"""Proxy metrics against known answers (the hidden grader's formulas are not published)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from deletion_audit.model import CaseResult, Link, LinkBasis, Step

POSITIONAL_FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
ALIGNED_FIELDS = ("state", "evidence_id")


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    cases: int
    status_accuracy: float
    step_precision: float
    step_recall: float
    step_f1: float
    key_precision: float
    key_recall: float
    key_f1: float
    step_row_accuracy: float
    output_row_accuracy: float
    field_accuracy: dict[str, float]
    positional_accuracy: dict[str, float]
    sequence_exact: float
    status_confusion: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LinkMetrics:
    precision: float
    recall: float
    by_basis: dict[LinkBasis, tuple[int, int]]  # basis -> (links made, links that are in the answer)
    missed: dict[str, tuple[str, ...]]
    extra: dict[str, tuple[str, ...]]


def score_cases(predicted: Mapping[str, CaseResult], gold: Mapping[str, CaseResult]) -> CaseMetrics:
    status_hits = sequence_hits = row_hits = 0
    tuple_hits = key_hits = predicted_steps = gold_steps = 0
    aligned = Counter[str]()
    aligned_total = 0
    positional = Counter[str]()
    confusion = Counter[tuple[str, str]]()
    for case_id, expected in gold.items():
        result = predicted.get(case_id)
        steps = result.steps if result else ()
        verdict = result.verdict.value if result else "MISSING"
        confusion[(expected.verdict.value, verdict)] += 1
        status_hits += verdict == expected.verdict.value
        sequence_hits += steps == expected.steps
        row_hits += verdict == expected.verdict.value and steps == expected.steps
        predicted_steps += len(steps)
        gold_steps += len(expected.steps)
        tuple_hits += _overlap(steps, expected.steps, _full)
        key_hits += _overlap(steps, expected.steps, _key)
        pairs = _align(steps, expected.steps)
        aligned_total += len(pairs)
        for mine, theirs in pairs:
            for name in ALIGNED_FIELDS:
                aligned[name] += getattr(mine, name) == getattr(theirs, name)
        for mine, theirs in zip(steps, expected.steps):
            for name in POSITIONAL_FIELDS:
                positional[name] += getattr(mine, name) == getattr(theirs, name)
    cases = len(gold)
    step_precision, step_recall = _ratio(tuple_hits, predicted_steps), _ratio(tuple_hits, gold_steps)
    key_precision, key_recall = _ratio(key_hits, predicted_steps), _ratio(key_hits, gold_steps)
    return CaseMetrics(
        cases=cases,
        status_accuracy=_ratio(status_hits, cases),
        step_precision=step_precision,
        step_recall=step_recall,
        step_f1=_f1(step_precision, step_recall),
        key_precision=key_precision,
        key_recall=key_recall,
        key_f1=_f1(key_precision, key_recall),
        step_row_accuracy=_ratio(tuple_hits, gold_steps),
        output_row_accuracy=_ratio(row_hits, cases),
        field_accuracy={name: _ratio(aligned[name], aligned_total) for name in ALIGNED_FIELDS},
        positional_accuracy={name: _ratio(positional[name], gold_steps) for name in POSITIONAL_FIELDS},
        sequence_exact=_ratio(sequence_hits, cases),
        status_confusion=dict(confusion),
    )


def score_links(links: Mapping[str, Iterable[Link]], gold: Mapping[str, CaseResult]) -> LinkMetrics:
    made = found = expected_total = 0
    by_basis: dict[LinkBasis, list[int]] = defaultdict(lambda: [0, 0])
    missed: dict[str, tuple[str, ...]] = {}
    extra: dict[str, tuple[str, ...]] = {}
    for case_id, expected in gold.items():
        wanted = {f"{s.system_id}/{s.resource_id}" for s in expected.steps}
        linked = {f"{link.resource.system_id}/{link.resource.resource_id}": link.basis
                  for link in links.get(case_id, ())}
        for name, basis in linked.items():
            by_basis[basis][0] += 1
            by_basis[basis][1] += name in wanted
        made += len(linked)
        expected_total += len(wanted)
        found += len(wanted & linked.keys())
        if wanted - linked.keys():
            missed[case_id] = tuple(sorted(wanted - linked.keys()))
        if linked.keys() - wanted:
            extra[case_id] = tuple(sorted(linked.keys() - wanted))
    return LinkMetrics(
        precision=_ratio(found, made),
        recall=_ratio(found, expected_total),
        by_basis={basis: (counts[0], counts[1]) for basis, counts in by_basis.items()},
        missed=missed,
        extra=extra,
    )


def _full(step: Step) -> tuple[str, ...]:
    return (step.phase, step.system_id, step.resource_id, step.operation, step.state, step.evidence_id)


def _key(step: Step) -> tuple[str, ...]:
    return (step.phase, step.system_id, step.resource_id, step.operation, step.state)


def _overlap(mine: Iterable[Step], theirs: Iterable[Step], key) -> int:
    return sum((Counter(map(key, mine)) & Counter(map(key, theirs))).values())


def _align(mine: tuple[Step, ...], theirs: tuple[Step, ...]) -> list[tuple[Step, Step]]:
    """Pairs predicted and expected steps that agree on phase, system, resource and operation."""
    remaining: dict[tuple[str, ...], list[Step]] = defaultdict(list)
    for step in theirs:
        remaining[_key(step)[:4]].append(step)
    pairs = []
    for step in mine:
        bucket = remaining.get(_key(step)[:4])
        if bucket:
            pairs.append((step, bucket.pop(0)))
    return pairs


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
