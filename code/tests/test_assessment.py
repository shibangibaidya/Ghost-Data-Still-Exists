import pytest

from conftest import DATASET_DIR
from deletion_audit.assessment import assess_case
from deletion_audit.assessment.state import ResourceStatus, resolve_state
from deletion_audit.assessment.policy import RuleBook
from deletion_audit.dataset import load_dataset
from deletion_audit.evidence import build_evidence_index
from deletion_audit.identity import IdentityDirectory
from deletion_audit.linking import link_cases
from deletion_audit.model import ResourceKey, Verdict
from deletion_audit.submission import parse_submission


@pytest.fixture(scope="module")
def world():
    dataset = load_dataset(DATASET_DIR)
    index = build_evidence_index(dataset)
    directory = IdentityDirectory(dataset.identities, dataset.identity_changes)
    cases = {case.case_id: case for case in dataset.sample_cases}
    subjects = {cid: directory.resolve(c.request_email, c.audit_time) for cid, c in cases.items()}
    book = link_cases([(c, subjects[cid]) for cid, c in cases.items()], index, dataset.activities)
    gold = {r.case_id: r for r in parse_submission((DATASET_DIR / "sample_output.csv").read_text(encoding="utf-8"))}
    return dataset, index, cases, subjects, book, gold


def state_of(world, case_id: str, system: str, resource: str):
    dataset, index, cases, subjects, _, _ = world
    return resolve_state(ResourceKey(system, resource), index.for_case(cases[case_id]))


def assess(world, case_id: str):
    dataset, index, cases, subjects, book, _ = world
    case = cases[case_id]
    return assess_case(case, subjects[case_id], book.links(case_id), index.for_case(case),
                       RuleBook.from_rules(dataset.rules))


def test_explicit_presence_beats_a_later_omission(world) -> None:
    state = state_of(world, "case_24", "account_service", "account_5334")

    assert state.status is ResourceStatus.PRESENT


def test_replica_copy_keeps_the_resource_present(world) -> None:
    state = state_of(world, "case_05", "account_service", "account_005")

    assert state.status is ResourceStatus.PRESENT
    assert state.presence_proof == "evidence_54"


def test_omission_from_a_snapshot_is_weak_absence(world) -> None:
    state = state_of(world, "case_01", "account_service", "account_001")

    assert state.status is ResourceStatus.WEAK_ABSENT
    assert state.absence_proof == "evidence_54"


def test_failed_claim_does_not_override_observed_absence(world) -> None:
    state = state_of(world, "case_09", "account_service", "account_3960")

    assert state.status is ResourceStatus.ABSENT
    # The absence is stated only in a file captured after audit_at, so the citation is the
    # 08:37 inventory that omits the account (D13); the gold row is INCOMPLETE and cites no
    # verification, and it cites the same inventory for the backup finding.
    assert state.absence_proof == "evidence_93"


@pytest.mark.parametrize("case_id", ["case_05", "case_11", "case_13", "case_16", "case_19"])
def test_cases_reachable_by_direct_links_match_the_sample_answer(world, case_id: str) -> None:
    gold = world[5][case_id]

    assessment = assess(world, case_id)

    assert assessment.result == gold


def test_no_linked_resources_is_incomplete_not_certified(world) -> None:
    dataset, index, cases, subjects, _, _ = world
    case = cases["case_01"]

    assessment = assess_case(case, subjects["case_01"], (), index.for_case(case), RuleBook.from_rules(dataset.rules))

    assert assessment.result.verdict is Verdict.INCOMPLETE and assessment.result.steps == ()
    assert "unlinked" in assessment.notes


def test_a_violation_known_only_from_an_uncitable_item_keeps_its_remediation(world) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    dataset, index, cases, subjects, book, _ = world
    # case_12 audited before evidence_01 is captured (11:59:39) but after its lines (custom_4).
    case = replace(cases["case_12"], audit_time=datetime(2026, 9, 15, 11, 59, tzinfo=UTC))
    assessment = assess_case(case, subjects["case_12"], book.links("case_12"), index.for_case(case),
                             RuleBook.from_rules(dataset.rules))

    steps = [(s.phase.value, s.operation, s.resource_id, s.evidence_id) for s in assessment.result.steps]
    assert assessment.result.verdict is Verdict.INCOMPLETE
    assert ("REMEDIATION", "DELETE_RECORD", "analytics_9914", "") in steps
    assert all(phase != "FINDING" and evidence == "" for phase, _, _, evidence in steps)


def _backup_plan(restore_allowed, status):
    from deletion_audit.assessment.planning import Exposure, plan_resource
    from deletion_audit.assessment.policy import Treatment
    from deletion_audit.assessment.state import ResourceState

    state = ResourceState(
        resource=ResourceKey("backup_service", "backup_1"), status=status, observations=(), content=(),
        presence_proof="evidence_32" if status is ResourceStatus.PRESENT else None, absence_proof=None,
        flow_enabled=None, flow_proof=None, restore_allowed=restore_allowed,
        restore_proof="evidence_93" if restore_allowed is not None else None,
    )
    return plan_resource(state, Treatment.BLOCK_RESTORE, Exposure(frozenset(), "user:user_1", view=None))


def test_a_present_backup_with_no_restore_flag_is_a_restore_risk() -> None:
    plan = _backup_plan(None, ResourceStatus.PRESENT)

    assert (plan.finding, plan.finding_proof, plan.remediation) == ("REPORT_RESTORE_RISK", "evidence_32", "BLOCK_RESTORE")


def test_a_backup_with_restore_blocked_is_verified_with_its_restore_proof() -> None:
    plan = _backup_plan(False, ResourceStatus.PRESENT)

    assert plan.finding is None
    assert (plan.verification, plan.verification_proof) == ("VERIFY_RESTORE_BLOCKED", "evidence_93")


def test_an_unresolved_backup_is_not_reported_as_a_risk() -> None:
    plan = _backup_plan(None, ResourceStatus.UNRESOLVED)

    assert plan.finding is None and plan.remediation is None


class _FakeView:
    """Minimal EvidenceView: everything up to AUDIT is citable."""

    AUDIT = 100

    def __init__(self, observations, snapshots):
        self._observations, self._snapshots = observations, snapshots
        self._citable = {o.citation_id: o.citable_at for o in observations}
        self._citable |= {s.citation_id: s.citable_at for s in snapshots}

    def observations_of(self, resource):
        return tuple(o for o in self._observations if o.resource == resource)

    def snapshots_of(self, system_id):
        return tuple(s for s in self._snapshots if s.system_id == system_id)

    def is_citable(self, citation_id):
        return self._citable[citation_id] <= self.AUDIT


def _observation(citation_id, known_at, citable_at, present):
    from deletion_audit.model import Observation, SourceKind

    return Observation(
        citation_id=citation_id, source_kind=SourceKind.EVIDENCE_ITEM, citable_at=citable_at, known_at=known_at,
        ordinal=known_at, explicit=True, resource=ResourceKey("account_service", "account_1"),
        copy_location="primary", present=present, identifiers=frozenset(), exposed=frozenset(),
        references=frozenset(), stated_category=None, stated_condition=None, flow_enabled=None,
        restore_allowed=None, retained_until=None, lineage=frozenset(), traits=frozenset(),
    )


def _snapshot(citation_id, known_at, ids=()):
    from deletion_audit.model import Snapshot

    return Snapshot(citation_id, known_at, known_at, "account_service", "primary", "customer_profiles",
                    frozenset(ids))


_RESOURCE = ResourceKey("account_service", "account_1")
_LATE_ABSENCE = _observation("evidence_late", known_at=95, citable_at=150, present=False)  # file captured after AUDIT


def test_an_uncitable_absence_is_cited_by_a_later_snapshot_that_omits_the_record() -> None:
    present = _observation("evidence_early", known_at=10, citable_at=10, present=True)
    view = _FakeView([present, _LATE_ABSENCE], [_snapshot("evidence_inventory", 90, {"account_2"})])

    state = resolve_state(_RESOURCE, view)

    assert (state.status, state.absence_proof) == (ResourceStatus.ABSENT, "evidence_inventory")


def test_a_snapshot_taken_before_the_record_was_last_seen_proves_nothing() -> None:
    present = _observation("evidence_early", known_at=50, citable_at=50, present=True)
    view = _FakeView([present, _LATE_ABSENCE], [_snapshot("evidence_inventory", 40)])

    assert resolve_state(_RESOURCE, view).absence_proof is None


def test_a_snapshot_that_holds_the_record_is_not_an_absence_proof() -> None:
    view = _FakeView([_LATE_ABSENCE], [_snapshot("evidence_inventory", 90, {"account_1"})])

    assert resolve_state(_RESOURCE, view).absence_proof is None


def test_an_uncitable_snapshot_is_not_an_absence_proof() -> None:
    view = _FakeView([_LATE_ABSENCE], [_snapshot("evidence_after_audit", 120)])

    assert resolve_state(_RESOURCE, view).absence_proof is None
