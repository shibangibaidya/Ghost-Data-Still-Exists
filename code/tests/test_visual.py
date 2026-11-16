import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deletion_audit.dataset import Activity, ManifestEntry
from deletion_audit.evidence.entries import to_observation
from deletion_audit.evidence.visual import (
    UnverifiedExtractionError,
    VisualReader,
    apply_corrections,
    catalog_aliases,
    to_entries,
)
from deletion_audit.llm import LlmClient, TransportReply, Usage

CAPTURED = datetime(2026, 9, 26, 11, tzinfo=UTC)
EXTRACTION = {
    "document_kind": "customer console",
    "visible_identifiers": ["user_11", "account_5762", "haruka.s1838@zoho.com", "CS-8538"],
    "records": [
        {"resource_id": "account_5762", "system": "Customer Operations", "record_type": None,
         "status": None, "observed_at": None,
         "fields": [{"name": "Email", "value": "haruka.s1838@zoho.com"}, {"name": "User", "value": "user_11"}]},
        {"resource_id": "CS-8538", "system": None, "record_type": None, "status": None,
         "observed_at": None, "fields": []},
    ],
}


def manifest(path: Path) -> ManifestEntry:
    return ManifestEntry("evidence_04", path, "account_admin_portal", CAPTURED)


class OneReply:
    def send(self, request):
        return TransportReply(EXTRACTION, Usage(1000, 300, 900), "vision-model", "STOP")


def test_records_become_entries_and_business_references_are_skipped(workdir: Path) -> None:
    (observation,) = [to_observation(e) for e in to_entries(manifest(workdir / "x.png"), EXTRACTION)]

    assert observation.resource.resource_id == "account_5762"
    assert observation.present is True
    assert "email:haruka.s1838@zoho.com" in observation.exposed
    assert "user:user_11" in observation.identifiers
    assert observation.known_at == CAPTURED


def test_corrections_overlay_the_raw_extraction() -> None:
    corrected = apply_corrections(EXTRACTION, [
        {"op": "set_field", "resource_id": "account_5762", "field": "Email",
         "extracted": "haruka.s1838@zoho.com", "corrected": "haruka.s1838@zoho.co"},
        {"op": "add_record", "record": {"resource_id": "support_9538", "system": None, "record_type": None,
                                        "status": None, "observed_at": None, "fields": []}},
    ])

    emails = [f["value"] for f in corrected["records"][0]["fields"] if f["name"] == "Email"]
    assert emails == ["haruka.s1838@zoho.co"]
    assert corrected["records"][-1]["resource_id"] == "support_9538"
    assert EXTRACTION["records"][0]["fields"][0]["value"] == "haruka.s1838@zoho.com"  # raw untouched


def test_unverified_extraction_is_refused_when_verification_is_required(workdir: Path) -> None:
    image = workdir / "console.png"
    image.write_bytes(b"fake png")
    reader = VisualReader(LlmClient(workdir / "cache", OneReply()), "vision-model",
                          workdir / "verification.json", require_verified=True)

    with pytest.raises(UnverifiedExtractionError, match="evidence_04"):
        reader.entries(manifest(image))

    sha = hashlib.sha256(b"fake png").hexdigest()
    (workdir / "verification.json").write_text(json.dumps({sha: {"file_id": "evidence_04", "corrections": []}}),
                                               encoding="utf-8")
    assert [e.resource_id for e in reader.entries(manifest(image))] == ["account_5762"]


def test_a_visual_record_is_known_from_its_capture_not_its_printed_dates(workdir: Path) -> None:
    extraction = {"document_kind": "pdf", "visible_identifiers": [], "records": [
        {"resource_id": "support_001", "system": None, "record_type": None, "status": None,
         "observed_at": "2026-06-18T10:24:00Z", "fields": []}]}

    (entry,) = to_entries(manifest(workdir / "x.pdf"), extraction)

    assert entry.known_at == CAPTURED  # "received 18 Jun" is history, not when it was seen


def test_printed_attachment_labels_map_to_the_vocabulary() -> None:
    extraction = {"document_kind": "pdf", "visible_identifiers": [], "records": [
        {"resource_id": "support_001", "system": None, "record_type": None, "status": None,
         "observed_at": None, "fields": [{"name": "file", "value": "a.pdf"}, {"name": "Uploader", "value": "customer"}]}]}

    (observation,) = [to_observation(e) for e in to_entries(manifest(Path("x.pdf")), extraction)]

    assert {"field:file_name", "field:uploaded_by"} <= observation.traits


def catalog(details: str, minute: int = 7) -> Activity:
    return Activity("activity_1", datetime(2026, 9, 26, 11, minute, tzinfo=UTC), "LINK_RESOURCE_IDENTITY",
                    "data_catalog", "export_service", "export_1842", "SUCCESS", details)


def test_catalog_aliases_are_exact_and_known_from_the_catalog_activity() -> None:
    aliases = catalog_aliases([
        catalog("Data catalog linked export_1842 (NST-2026-0718-1842) to a@b.com and user_18."),
        catalog("Data catalog linked search_428 (search_428) to a@b.com and user_13."),
    ])
    extraction = {"document_kind": "statement", "visible_identifiers": [], "records": [
        {"resource_id": "user_18", "system": None, "record_type": "Customer statement", "status": None,
         "observed_at": None, "fields": [{"name": "Detail", "value": "statement NST-2026-0718-1842"}]},
        {"resource_id": "user_19", "system": None, "record_type": None, "status": None,
         "observed_at": None, "fields": [{"name": "Detail", "value": "statement NST-2026-0718-18420"}]},
    ]}

    (entry,) = to_entries(manifest(Path("x.pdf")), extraction, aliases)

    assert set(aliases) == {"NST-2026-0718-1842"}  # an alias equal to its resource id adds nothing
    assert entry.resource_id == "export_1842"
    assert entry.citable_at == CAPTURED
    assert entry.known_at == datetime(2026, 9, 26, 11, 7, tzinfo=UTC)
    assert to_entries(manifest(Path("x.pdf")), extraction) == []  # no catalog, no resource


def test_a_healthy_connector_is_an_active_flow() -> None:
    extraction = {"document_kind": "console", "visible_identifiers": [], "records": [
        {"resource_id": "data_flow_1121", "system": None, "record_type": None, "status": "Healthy",
         "observed_at": None, "fields": [{"name": "Mode", "value": "Continuous"}]}]}

    (observation,) = [to_observation(e) for e in to_entries(manifest(Path("x.png")), extraction)]

    assert observation.flow_enabled is True
