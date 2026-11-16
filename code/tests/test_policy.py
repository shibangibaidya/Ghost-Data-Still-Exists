from dataclasses import replace
from datetime import UTC, datetime

import pytest

from conftest import DATASET_DIR
from deletion_audit.assessment.policy import RuleBook, Treatment, data_category, known_policy_actions
from deletion_audit.dataset import Activity, load_dataset
from deletion_audit.model import Case, Observation, ResourceKey, SourceKind

T = datetime(2026, 9, 1, tzinfo=UTC)


def obs(system: str, resource: str, *, category: str | None = None, traits: set[str] = frozenset()) -> Observation:
    return Observation(
        citation_id="evidence_x", source_kind=SourceKind.EVIDENCE_ITEM, citable_at=T, known_at=T,
        ordinal=0, explicit=True, resource=ResourceKey(system, resource), copy_location="primary",
        present=True, identifiers=frozenset(), exposed=frozenset(), references=frozenset(),
        stated_category=category, stated_condition=None, flow_enabled=None, restore_allowed=None,
        retained_until=None, lineage=frozenset(), traits=frozenset(traits),
    )


@pytest.fixture(scope="module")
def rules() -> RuleBook:
    return RuleBook.from_rules(load_dataset(DATASET_DIR).rules)


@pytest.mark.parametrize(
    ("observation", "category", "treatment"),
    [
        (obs("account_service", "account_1"), "USER_PROFILE", Treatment.DELETE),
        (obs("search_service", "search_1"), "SEARCH_INDEX_ENTRY", Treatment.DELETE),
        (obs("support_platform", "support_1", category="SUPPORT_ATTACHMENT"), "SUPPORT_ATTACHMENT", Treatment.DELETE),
        (obs("support_platform", "support_1", traits={"field:message_text"}), "SUPPORT_MESSAGE", Treatment.REDACT),
        (obs("support_platform", "support_1", traits={"field:file_name", "storage_class:ticket_attachment"}), "SUPPORT_ATTACHMENT", Treatment.DELETE),
        (obs("support_platform", "support_1", traits={"event:attachment_pii_scan", "field:file", "field:message_excerpt"}), "SUPPORT_ATTACHMENT", Treatment.DELETE),
        (obs("support_platform", "support_1", traits={"event:contact_query_completed", "source:legacy_contact_index"}), "SECONDARY_IDENTIFIER", Treatment.DELETE),
        (obs("analytics_platform", "analytics_1"), "ANALYTICS_PROFILE", Treatment.ANONYMIZE),
        (obs("analytics_platform", "analytics_1", traits={"event:contact_lookup_completed"}), "SECONDARY_IDENTIFIER", Treatment.DELETE),
        (obs("export_service", "export_1"), "USER_EXPORT", Treatment.DELETE),
        (obs("security_service", "audit_record_1"), "SECURITY_AUDIT_LOG", Treatment.RETAIN),
        (obs("legal_service", "legal_hold_1"), "LEGAL_HOLD_RECORD", Treatment.RETAIN),
        (obs("identity_service", "tombstone_1"), "ACCOUNT_TOMBSTONE", Treatment.REDACT),
        (obs("backup_service", "backup_1"), "BACKUP_ARCHIVE", Treatment.BLOCK_RESTORE),
        (obs("integration_service", "data_flow_1"), "DATA_FLOW", Treatment.DISABLE),
        (obs("marketing_platform", "marketing_1"), "MARKETING_PROFILE", Treatment.DELETE),
        (obs("email_delivery_service", "email_delivery_1"), "EMAIL_DELIVERY_LOG", Treatment.REDACT),
        (obs("cache_service", "cache_1"), "CACHE_ENTRY", Treatment.DELETE),
    ],
)
def test_category_and_treatment(rules, observation, category, treatment) -> None:
    found = data_category(observation.resource, [observation])

    assert found == category
    assert rules.treatment(found) is treatment


def test_a_screen_label_is_not_a_stated_category(rules) -> None:
    labelled = obs("identity_service", "tombstone_17", category="Customer key")

    assert data_category(labelled.resource, [labelled], rules.categories) == "ACCOUNT_TOMBSTONE"


MESSAGE = obs("support_platform", "support_1", traits={"field:message_excerpt"})


@pytest.mark.parametrize(
    ("observation", "action", "category"),
    [
        (MESSAGE, None, "SUPPORT_MESSAGE"),
        (MESSAGE, "REDACT", "SUPPORT_MESSAGE"),
        (MESSAGE, "DELETE", "SUPPORT_ATTACHMENT"),
        (obs("analytics_platform", "analytics_1"), "DELETE", "SECONDARY_IDENTIFIER"),
        (obs("analytics_platform", "analytics_1"), "ANONYMIZE", "ANALYTICS_PROFILE"),
        # A stated category is never overridden, and a System with one category keeps it.
        (obs("support_platform", "support_1", category="SUPPORT_MESSAGE"), "DELETE", "SUPPORT_MESSAGE"),
        (obs("security_service", "audit_record_1"), "DELETE", "SECURITY_AUDIT_LOG"),
        (MESSAGE, "RETAIN", "SUPPORT_MESSAGE"),
    ],
)
def test_worker_action_picks_among_the_system_categories(rules, observation, action, category) -> None:
    assert rules.category(observation.resource, [observation], action) == category


def _activity(event_type: str, resource_id: str, day: int) -> Activity:
    return Activity(f"activity_{day}", datetime(2026, 9, day, tzinfo=UTC), event_type, "deletion_worker",
                    "support_platform", resource_id, "FAILED", "")


def test_policy_actions_are_those_known_within_the_episode() -> None:
    case = Case("case_x", "a@example.com", datetime(2026, 9, 10, tzinfo=UTC), "COMPLETE",
                datetime(2026, 9, 12, tzinfo=UTC), datetime(2026, 9, 13, tzinfo=UTC), "delete me")
    activities = [
        _activity("DELETE_RECORD", "support_1", 11),
        _activity("DELETE_RECORD", "support_2", 9),    # before the request
        _activity("DELETE_RECORD", "support_3", 14),   # after the audit
        _activity("DELETE_RECORD", "support_4", 11),
        _activity("REDACT_RECORD", "support_4", 12),   # two different actions: none
        _activity("INDEX_RECORD", "search_1", 11),     # not a policy action
    ]

    assert known_policy_actions(activities, case) == {"support_1": "DELETE"}


def test_rules_are_data_not_code() -> None:
    dataset_rules = load_dataset(DATASET_DIR).rules
    changed = [replace(rule, required_action="REDACT") if rule.data_category == "USER_EXPORT" else rule
               for rule in dataset_rules]

    assert RuleBook.from_rules(changed).treatment("USER_EXPORT") is Treatment.REDACT


def test_unknown_category_is_an_error(rules) -> None:
    with pytest.raises(KeyError):
        rules.treatment("NOT_A_CATEGORY")
