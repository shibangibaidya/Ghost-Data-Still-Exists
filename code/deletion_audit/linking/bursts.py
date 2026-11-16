"""Burst Attribution (ADR-0001): deletion-worker activity around a case's reported time.

Measured on resources that direct links already tie to one case (all 550 requests):
- on-the-hour slots: account deletions run 47-72 min before `reported_at`; the other steps
  run at fixed marks 29-7 min before it (successes) or 38-130 min after it (retries);
- off-the-hour slots (samples 01-10): everything within about -4..+10 min;
- the account deletion's result matches the reported status in 248 of 250 cases
  (COMPLETE/SUCCESS, PARTIALLY_COMPLETE/PARTIAL, NOT_STARTED/FAILED).
Activities of different cases in one slot cannot be told apart by their order or spacing
with enough precision (pairwise spacing separates them at about 0.88), and the worker also
handles records of people who filed no request in these files. So a Burst only gives a
claimant its missing account deletion, and only when the claim-consistency match is unique.
On the 32 samples, attributing other resource types this way gave 0 correct links and 2 wrong.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from deletion_audit.dataset import Activity
from deletion_audit.evidence import vocabulary as vocab
from deletion_audit.model import Case, ResourceKey, Subject

EVENT_TYPES = frozenset({
    "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "RETAIN_RECORD",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS",
})
# Windows in minutes relative to reported_at, with a margin around the measured ranges.
_ON_HOUR_ACCOUNT = ((-80.0, -40.0),)
_ON_HOUR_OTHER = ((-31.0, -2.0), (35.0, 135.0))
_OFF_HOUR = ((-10.0, 15.0),)
_STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}

Claimant = tuple[Case, Subject]


def fits(activity: Activity, slot: datetime) -> bool:
    minutes = (activity.event_time - slot) / timedelta(minutes=1)
    if slot.minute == 0 and slot.second == 0:
        windows = _ON_HOUR_ACCOUNT if _is_account_deletion(activity) else _ON_HOUR_OTHER
    else:
        windows = _OFF_HOUR
    return any(low <= minutes <= high for low, high in windows)


def burst_links(
    case: Case,
    subject: Subject,
    own: set[ResourceKey],
    rivals: Sequence[tuple[Claimant, set[ResourceKey]]],
    known_activities: Iterable[Activity],
    slots: set[datetime],
) -> list[tuple[ResourceKey, str]]:
    """Resources of the case's Burst. `rivals` are the other claimants in the same slot, each
    with the resources direct links reserve for them as far as this case may know."""
    reserved = set().union(*(links for _, links in rivals)) if rivals else set()
    candidates = [
        activity for activity in known_activities
        if activity.source_system == "deletion_worker"
        and activity.event_type in EVENT_TYPES
        and fits(activity, case.reported_at)
        and not any(fits(activity, other) for other in slots if other != case.reported_at)
        and _resource(activity) not in reserved | own
    ]
    if any(resource.system_id == "account_service" for resource in own):
        return []
    deletions = [activity for activity in candidates if _is_account_deletion(activity)]
    mine = [activity for activity in deletions if _consistent(case, activity)]
    if not rivals and len(deletions) == 1 and not mine:
        mine = deletions  # a lone claimant and a lone deletion: the 2-in-250 status mismatch
    needy_rivals = [rival for (rival, _), links in rivals
                    if not any(resource.system_id == "account_service" for resource in links)]
    if len(mine) == 1 and not any(_consistent(rival, mine[0]) for rival in needy_rivals):
        return [(_resource(mine[0]), mine[0].activity_id)]
    return []


def _consistent(case: Case, activity: Activity) -> bool:
    return _STATUS_RESULT.get(case.reported_status) == activity.reported_result


def _is_account_deletion(activity: Activity) -> bool:
    return activity.target_system == "account_service" and activity.event_type == "DELETE_RECORD"


def _resource(activity: Activity) -> ResourceKey:
    return ResourceKey(vocab.system_for(activity.resource_id), activity.resource_id)
