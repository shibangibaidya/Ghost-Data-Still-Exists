from datetime import UTC, datetime

import pytest

from conftest import DATASET_DIR
from deletion_audit.dataset import load_dataset
from deletion_audit.evidence import build_evidence_index
from deletion_audit.evidence.entries import extract_tokens
from deletion_audit.model import ResourceKey, SourceKind


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET_DIR)


@pytest.fixture(scope="module")
def index(dataset):
    return build_evidence_index(dataset)


def observations(index, system: str, resource: str):
    return [o for o in index.all_observations() if o.resource == ResourceKey(system, resource)]


def case(dataset, case_id: str):
    return next(c for c in dataset.sample_cases + dataset.cases if c.case_id == case_id)


def test_every_text_evidence_file_parses_and_visuals_wait_for_the_vlm(index) -> None:
    assert len(index.parsed_files) == 79
    assert len(index.skipped_files) == 15


def test_token_extraction_uses_whole_token_boundaries() -> None:
    tokens = extract_tokens("ref user_54 not user_541, mail BCARVALHO6517@GMAIL.COM. done")

    assert "user:user_54" in tokens and "user:user_541" in tokens
    assert "user:user_5" not in tokens
    assert "email:bcarvalho6517@gmail.com" in tokens


def test_replica_drift_is_kept_per_copy(index) -> None:
    found = observations(index, "account_service", "account_005")

    assert {(o.citation_id, o.copy_location, o.present) for o in found} == {
        ("evidence_54", "failover_replica", True)
    }
    primary = [s for s in index.all_snapshots()
               if s.citation_id == "evidence_54" and s.copy_location == "primary"]
    assert primary and all("account_005" not in s.resource_ids for s in primary)


def test_empty_snapshot_section_is_still_a_snapshot(index) -> None:
    sections = [s for s in index.all_snapshots()
                if s.citation_id == "evidence_51" and s.system_id == "account_service"]

    assert len(sections) == 1 and sections[0].resource_ids == frozenset()


def test_tombstone_exposure_and_reference(index) -> None:
    (tombstone,) = observations(index, "identity_service", "tombstone_9178")

    assert "email:addo.kofi@gmail.com" in tombstone.exposed
    assert "user:user_542" in tombstone.references
    assert "status:DELETED" in tombstone.traits


def test_log_trace_attaches_lookup_key_and_keeps_line_time(index) -> None:
    (lookup,) = [o for o in observations(index, "account_service", "account_6523")
                 if o.citation_id == "evidence_67"]

    assert lookup.present is False
    assert "email:leonie.g9352@outlook.com" in lookup.identifiers
    assert "email:leonie.g9352@outlook.com" not in lookup.exposed


def test_cache_miss_in_a_trace_does_not_hide_the_read_result(index) -> None:
    # evidence_06 trace tr-4877543: cache_lookup result=miss, then the read returns rows=1.
    (record,) = observations(index, "security_service", "audit_record_2877")

    assert record.present is True


def test_entry_time_versus_capture_time(index, dataset) -> None:
    (record,) = [o for o in observations(index, "security_service", "audit_record_4902")]
    view = index.for_case(case(dataset, "case_09"))

    assert record.known_at == datetime(2026, 9, 16, 8, 52, 59, tzinfo=UTC)
    assert record.citable_at == datetime(2026, 9, 18, 11, 55, 24, tzinfo=UTC)
    assert record in view.observations_of(record.resource)
    assert not view.is_citable("evidence_06")
    assert view.is_citable("evidence_93")


def test_view_never_returns_entries_after_audit_or_before_request(index, dataset) -> None:
    for item in dataset.sample_cases:
        view = index.for_case(item)
        for observation in view.all_observations():
            assert item.requested_at <= observation.known_at <= item.audit_time


def test_old_hash_contact_lookup(index) -> None:
    (contact,) = observations(index, "analytics_platform", "analytics_9914")

    assert "hash:44a65154bfd8e408852578fbd76813d094d1944f255ac1769ee81ba90492f10e" in contact.identifiers
    assert "event:contact_lookup_completed" in contact.traits
    assert contact.present is True


def test_handover_rows_state_category_and_condition(index) -> None:
    attachment = [o for o in observations(index, "support_platform", "support_4218") if o.citation_id == "evidence_03"]
    message = [o for o in observations(index, "support_platform", "support_864") if o.citation_id == "evidence_03"]

    assert attachment[0].stated_category == "SUPPORT_ATTACHMENT"
    assert message[0].stated_condition == "IDENTIFIABLE"
    assert "email:sipho.mokoena@gmail.com" in [
        o for o in observations(index, "integration_service", "data_flow_7495") if o.citation_id == "evidence_03"
    ][0].identifiers


def test_service_export_lookups(index) -> None:
    present = [o for o in observations(index, "account_service", "account_5334") if o.citation_id == "evidence_92"]
    absent = [o for o in observations(index, "account_service", "account_3136") if o.citation_id == "evidence_92"]
    flow = [o for o in observations(index, "integration_service", "data_flow_2295") if o.citation_id == "evidence_92"]

    assert present[0].present is True and "email:keiko.nakamura@gmail.com" in present[0].exposed
    assert absent[0].present is False
    assert flow[0].flow_enabled is False


def test_restore_and_flow_state_from_logs(index) -> None:
    backup = [o for o in observations(index, "backup_service", "backup_3245") if o.citation_id == "evidence_01"]

    assert backup[0].restore_allowed is True


def test_check_activities_become_low_rank_observations(index) -> None:
    checks = [o for o in observations(index, "integration_service", "data_flow_9846")
              if o.source_kind is SourceKind.CHECK_ACTIVITY]

    assert len(checks) == 1 and checks[0].flow_enabled is True
    assert checks[0].citation_id.startswith("activity_")


def test_search_document_lineage(index) -> None:
    (document,) = [o for o in observations(index, "search_service", "search_001") if o.citation_id == "evidence_57"]

    assert ResourceKey("account_service", "account_006") in document.lineage


def test_sql_integer_flags_are_booleans(index) -> None:
    # evidence_93: backup_restore_entries.restore_allowed is INTEGER 0/1, and backup_4978 holds '1'.
    (row,) = [o for o in observations(index, "backup_service", "backup_4978") if o.citation_id == "evidence_93"]

    assert row.restore_allowed is True
