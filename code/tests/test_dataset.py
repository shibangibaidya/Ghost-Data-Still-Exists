from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import DATASET_DIR
from deletion_audit.dataset import DatasetError, load_dataset, read_cases


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET_DIR)


def test_real_dataset_counts(dataset) -> None:
    assert len(dataset.cases) == 518
    assert len(dataset.sample_cases) == 32
    assert len(dataset.activities) == 4011
    assert len(dataset.rules) == 15
    assert len(dataset.manifest) == 94
    assert len(dataset.identities) == 850
    assert len(dataset.identity_changes) == 306


def test_cases_are_in_numeric_case_id_order(dataset) -> None:
    ids = [case.case_id for case in dataset.cases]
    assert ids[:3] == ["case_33", "case_34", "case_35"]
    assert ids[-1] == "case_550"
    assert ids.index("case_99") < ids.index("case_100")


def test_case_fields_are_parsed(dataset) -> None:
    case = dataset.sample_cases[0]
    assert case.case_id == "case_01"
    assert case.request_email == "amina.elmasri@gmail.com"
    assert case.audit_time == datetime(2026, 9, 3, 11, 30, tzinfo=UTC)
    assert case.reported_status == "COMPLETE"


def test_citation_time_covers_files_activities_and_identity_changes(dataset) -> None:
    assert dataset.citation_time("evidence_54") == datetime(2026, 9, 2, 23, 0, tzinfo=UTC)
    assert dataset.citation_time("activity_01") == datetime(2026, 1, 4, 14, 56, 29, tzinfo=UTC)
    assert dataset.citation_time("audit_01") == datetime(2024, 12, 14, 16, 31, 44, tzinfo=UTC)
    assert dataset.citation_time("evidence_999") is None


def test_manifest_paths_point_to_real_files(dataset) -> None:
    assert all(entry.path.is_file() for entry in dataset.manifest)


def test_non_ascii_request_text_survives(dataset) -> None:
    texts = [case.request_text for case in dataset.cases]
    assert any("このメールアドレス" in text for text in texts)


def test_malformed_timestamp_names_file_and_row(workdir: Path) -> None:
    requests = workdir / "requests.csv"
    requests.write_text(
        "case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\r\n"
        "case_1,a@b.com,2026-01-01T00:00:00Z,COMPLETE,2026-01-02T00:00:00Z,2026-01-03T00:00:00Z,x\r\n"
        "case_2,c@d.com,not-a-time,COMPLETE,2026-01-02T00:00:00Z,2026-01-03T00:00:00Z,y\r\n",
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(DatasetError, match=r"requests\.csv.*row 3"):
        read_cases(requests)


def test_missing_column_is_reported(workdir: Path) -> None:
    requests = workdir / "requests.csv"
    requests.write_text("case_id,email\ncase_1,a@b.com\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="missing columns"):
        read_cases(requests)


def test_duplicate_case_id_is_rejected(workdir: Path) -> None:
    requests = workdir / "requests.csv"
    row = "case_1,a@b.com,2026-01-01T00:00:00Z,COMPLETE,2026-01-02T00:00:00Z,2026-01-03T00:00:00Z,x\n"
    requests.write_text(
        "case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n" + row + row,
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="duplicate"):
        read_cases(requests)
