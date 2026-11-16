"""Data Category and Treatment. The rules come from deletion_rules.csv; this module only
decides which category a Resource belongs to (FR-8, FR-8a, D11)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from deletion_audit.dataset import Activity, Rule
from deletion_audit.model import Case, Observation, ResourceKey


class Treatment(StrEnum):
    DELETE = "DELETE"
    REDACT = "REDACT"
    ANONYMIZE = "ANONYMIZE"
    RETAIN = "RETAIN"
    BLOCK_RESTORE = "BLOCK_RESTORE"  # a retained backup whose restore access must be blocked
    DISABLE = "DISABLE"              # a data flow must not copy or recreate the data


# Category of a Resource when the evidence does not state one.
SYSTEM_CATEGORIES = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "integration_service": "DATA_FLOW",
    "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD",
    "marketing_platform": "MARKETING_PROFILE",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "cache_service": "CACHE_ENTRY",
    "object_storage": "SUPPORT_ATTACHMENT",
}
# A log's `file=` names the scanned file, as the `file` label does on a screen (VISUAL_FIELD_ALIASES).
_ATTACHMENT_TRAITS = frozenset({"field:file_name", "field:file", "storage_class:ticket_attachment",
                                "collection:ticket_attachments", "field:uploaded_by"})
_MESSAGE_TRAITS = frozenset({"field:message_text", "field:message_excerpt",
                             "collection:support_conversations"})
_CONTACT_EVENTS = ("event:contact_lookup", "event:contact_query")
_CONTACT_SOURCES = frozenset({"source:legacy_contact_index"})
# Worker Activities that apply one policy action to one record (D14).
POLICY_ACTIVITIES = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT",
                     "ANONYMIZE_RECORD": "ANONYMIZE", "RETAIN_RECORD": "RETAIN"}
# The Data Categories a System can hold, in the order data_category tries them.
_SYSTEM_ALTERNATIVES = {
    "support_platform": ("SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER"),
    "analytics_platform": ("ANALYTICS_PROFILE", "SECONDARY_IDENTIFIER"),
}


def stated_category(
    observations: Sequence[Observation], known: Collection[str] | None = None
) -> str | None:
    # A screen label such as "Customer key" is not a policy category; only `known` ones are stated.
    stated = [o.stated_category for o in observations
              if o.stated_category and (known is None or o.stated_category in known)]
    return stated[-1] if stated else None


def known_policy_actions(activities: Iterable[Activity], case: Case) -> dict[str, str]:
    """D14: the one policy action the deletion worker applied to each record within the case's
    Episode. A record that received two different actions has none."""
    found: dict[str, set[str]] = defaultdict(set)
    for activity in activities:
        action = POLICY_ACTIVITIES.get(activity.event_type)
        if action and case.requested_at <= activity.event_time <= case.audit_time:
            found[activity.resource_id].add(action)
    return {resource_id: actions.pop() for resource_id, actions in found.items() if len(actions) == 1}


def data_category(
    resource: ResourceKey, observations: Sequence[Observation], known: Collection[str] | None = None
) -> str:
    stated = stated_category(observations, known)
    if stated:
        return stated
    traits = set().union(*(o.traits for o in observations)) if observations else set()
    is_contact = bool(traits & _CONTACT_SOURCES) or any(t.startswith(_CONTACT_EVENTS) for t in traits)
    if resource.system_id == "support_platform":
        if traits & _ATTACHMENT_TRAITS:
            return "SUPPORT_ATTACHMENT"
        if traits & _MESSAGE_TRAITS or any("ticket" in t or "message" in t for t in traits):
            return "SUPPORT_MESSAGE"
        if is_contact:
            return "SECONDARY_IDENTIFIER"
    if resource.system_id == "analytics_platform" and is_contact:
        # A contact lookup keyed by an identifier is an alternate identifier, not a profile.
        return "SECONDARY_IDENTIFIER"
    return SYSTEM_CATEGORIES[resource.system_id]


@dataclass(frozen=True)
class RuleBook:
    actions: dict[str, str]

    @classmethod
    def from_rules(cls, rules: Iterable[Rule]) -> RuleBook:
        return cls({rule.data_category: rule.required_action for rule in rules})

    @property
    def categories(self) -> frozenset[str]:
        return frozenset(self.actions) | {"DATA_FLOW"}

    def category(
        self, resource: ResourceKey, observations: Sequence[Observation], action: str | None = None
    ) -> str:
        """D14: when no category is stated, the worker's policy action on the record picks among
        the categories its System can hold. The action is the workflow's reading of the rules for
        that record; its result is still only a claim."""
        inferred = data_category(resource, observations, self.categories)
        if action is None or self.actions.get(inferred) == action:
            return inferred
        if stated_category(observations, self.categories):
            return inferred
        matching = [category for category in _SYSTEM_ALTERNATIVES.get(resource.system_id, ())
                    if self.actions.get(category) == action]
        return matching[0] if matching else inferred

    def treatment(self, category: str) -> Treatment:
        if category == "DATA_FLOW":
            return Treatment.DISABLE
        action = self.actions[category]
        if category == "BACKUP_ARCHIVE" and action == "RETAIN":
            return Treatment.BLOCK_RESTORE
        return Treatment(action)
