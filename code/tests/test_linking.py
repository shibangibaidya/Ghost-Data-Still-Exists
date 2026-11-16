import random

import pytest

from conftest import DATASET_DIR
from deletion_audit.dataset import load_dataset
from deletion_audit.evidence import build_evidence_index
from deletion_audit.identity import IdentityDirectory
from deletion_audit.linking import link_cases
from deletion_audit.model import LinkBasis


@pytest.fixture(scope="module")
def world():
    dataset = load_dataset(DATASET_DIR)
    index = build_evidence_index(dataset)
    directory = IdentityDirectory(dataset.identities, dataset.identity_changes)
    pairs = [(case, directory.resolve(case.request_email, case.audit_time)) for case in dataset.sample_cases]
    return dataset, index, pairs


@pytest.fixture(scope="module")
def book(world):
    dataset, index, pairs = world
    return link_cases(pairs, index, dataset.activities)


def linked(book, case_id: str) -> dict[str, LinkBasis]:
    return {link.resource.resource_id: link.basis for link in book.links(case_id)}


def test_replica_copy_is_linked_by_identifier(book) -> None:
    assert linked(book, "case_05")["account_005"] is LinkBasis.IDENTIFIER


def test_old_email_hash_links_contact_record(book) -> None:
    assert "analytics_9914" in linked(book, "case_12")


def test_lineage_links_the_source_account_of_a_search_entry(book) -> None:
    links = linked(book, "case_06")
    assert links["search_001"] is LinkBasis.IDENTIFIER
    assert links["account_006"] in (LinkBasis.IDENTIFIER, LinkBasis.LINEAGE)


def test_similar_email_decoy_is_not_linked(book) -> None:
    assert not any(resource.startswith("search_") for resource in linked(book, "case_28"))


def test_earlier_episode_of_the_same_subject_is_not_linked(book) -> None:
    assert "account_6523" not in linked(book, "case_17")
    assert "tombstone_17" not in linked(book, "case_17")


def test_reference_match_by_stable_reference(book) -> None:
    assert linked(book, "case_31")["tombstone_440"] in (LinkBasis.REFERENCE, LinkBasis.IDENTIFIER)
    assert linked(book, "case_31")["audit_record_6909"] is LinkBasis.REFERENCE


def test_unresolved_subject_has_no_links(world) -> None:
    dataset, index, pairs = world
    case, _ = pairs[0]

    book = link_cases([(case, None)], index, dataset.activities)

    assert book.links(case.case_id) == ()


@pytest.fixture(scope="module")
def burst_book(world):
    dataset, index, pairs = world
    directory = IdentityDirectory(dataset.identities, dataset.identity_changes)
    context = [(case, directory.resolve(case.request_email, case.audit_time)) for case in dataset.cases]
    return link_cases(pairs, index, dataset.activities, context=context)


def test_single_claimant_slot_attributes_its_burst(burst_book) -> None:
    links = linked(burst_book, "case_01")
    assert links["account_001"] is LinkBasis.BURST


def test_rival_with_a_known_account_leaves_the_remaining_deletion_unambiguous(burst_book) -> None:
    # case_25 and case_26 share a slot and are both NOT_STARTED with two FAILED deletions, but an
    # entry known before case_25's audit time ties account_5684 to case_26.
    assert linked(burst_book, "case_25")["account_3136"] is LinkBasis.BURST
    assert linked(burst_book, "case_26")["account_5684"] is not LinkBasis.BURST


def test_rivals_known_only_from_later_evidence_make_the_case_abstain(burst_book) -> None:
    # case_31's slot has three COMPLETE claimants; what reserves the other two accounts was
    # captured after case_31's audit time, so the SUCCESS deletions stay ambiguous for it.
    assert "account_4857" not in linked(burst_book, "case_31")
    assert not any(resource.startswith("account_") for resource in linked(burst_book, "case_31"))


def test_case_with_a_direct_account_takes_no_burst(burst_book) -> None:
    assert not [link for link in burst_book.links("case_11") if link.basis is LinkBasis.BURST]
    assert not [link for link in burst_book.links("case_12") if link.basis is LinkBasis.BURST]


def test_bursts_never_take_a_resource_reserved_by_a_direct_link(burst_book) -> None:
    # account_5334 is linked directly to case_24 (same slot as case_25 and case_26).
    assert "account_5334" not in linked(burst_book, "case_25")
    assert "account_5334" not in linked(burst_book, "case_26")


def test_links_do_not_depend_on_input_order(world, book) -> None:
    dataset, index, pairs = world
    shuffled = list(pairs)
    random.Random(7).shuffle(shuffled)

    again = link_cases(shuffled, index, dataset.activities)

    assert all(again.links(case.case_id) == book.links(case.case_id) for case, _ in pairs)
