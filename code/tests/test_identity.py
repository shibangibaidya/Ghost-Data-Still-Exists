from datetime import UTC, datetime

import pytest

from conftest import DATASET_DIR
from deletion_audit.dataset import IdentityChange, IdentityRow, load_dataset
from deletion_audit.identity import IdentityDirectory

BEFORE = datetime(2026, 1, 1, tzinfo=UTC)
CHANGED = datetime(2026, 3, 1, tzinfo=UTC)
AFTER = datetime(2026, 6, 1, tzinfo=UTC)

ROW = IdentityRow("user_7", "Ana Silva", "ana.new@example.com", "+55 11 9999-0000", "HASHNEW", BEFORE)
CHANGES = (
    IdentityChange("audit_1", "user_7", CHANGED, "email", "ana.old@example.com", "ana.new@example.com"),
    IdentityChange("audit_2", "user_7", CHANGED, "email_hash", "hashold", "hashnew"),
    IdentityChange("audit_3", "user_7", CHANGED, "phone", "+55 11 1111-2222", "+55 11 9999-0000"),
)


@pytest.fixture
def directory() -> IdentityDirectory:
    return IdentityDirectory((ROW,), CHANGES)


def test_current_email_resolves_with_full_identifier_set(directory) -> None:
    subject = directory.resolve("ana.new@example.com", AFTER)

    assert subject is not None and subject.user_id == "user_7"
    ids = subject.identifiers
    assert ids.emails == {"ana.new@example.com", "ana.old@example.com"}
    assert ids.email_hashes == {"hashnew", "hashold"}
    assert ids.phones == {"551199990000", "551111112222"}
    assert ids.names == {"ana silva"}


def test_historical_email_resolves_case_insensitively(directory) -> None:
    subject = directory.resolve("  ANA.OLD@Example.com ", AFTER)

    assert subject is not None and subject.user_id == "user_7"


def test_identifiers_introduced_after_the_cutoff_are_not_known(directory) -> None:
    subject = directory.resolve("ana.old@example.com", BEFORE)

    assert subject is not None
    assert subject.identifiers.emails == {"ana.old@example.com"}
    assert subject.identifiers.email_hashes == {"hashold"}
    assert subject.identifiers.phones == {"551111112222"}


def test_unknown_email_resolves_to_nobody(directory) -> None:
    assert directory.resolve("someone@else.com", AFTER) is None


def test_every_official_request_email_resolves_to_one_subject() -> None:
    dataset = load_dataset(DATASET_DIR)
    directory = IdentityDirectory(dataset.identities, dataset.identity_changes)
    current_emails = {row.email.lower() for row in dataset.identities}

    subjects = [directory.resolve(case.request_email, case.audit_time) for case in dataset.cases]

    assert all(subject is not None for subject in subjects)
    by_current = sum(case.request_email.lower() in current_emails for case in dataset.cases)
    assert (by_current, len(dataset.cases) - by_current) == (418, 100)
    assert all(not subject.ambiguous_with for subject in subjects)
