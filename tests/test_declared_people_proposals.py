"""Declared bulk deletion produces one frozen draft and never applies topology in the service."""

import pytest

from meridian.control.errors import ServiceError
from meridian.control.models import RevisionProposal, UserCreate, UserUpdate
from meridian.control.tokens import ROOT
from meridian.core.agent_types import canonical_sha256
from tests.support.people_proposals import commit_topology
from tests.support.topology import single_exit_intent
from tests.test_control_people_bulk import commit, preview
from tests.test_control_people_query import people_service as people_service
from tests.test_control_people_query import seed


@pytest.fixture
def committed_people(people_service):
    seed(people_service)
    people_service.create_user(UserCreate(username="p-kept"))
    intent = single_exit_intent(users=["p-b", "p-d", "p-kept"])
    revision = commit_topology(people_service, intent)
    return people_service, intent, revision


def test_declared_bulk_delete_has_one_secret_free_proposal_and_one_service_transaction(committed_people, monkeypatch):
    service, intent, revision = committed_people
    service.update_user("p-b", UserUpdate(note="Private removal sample", tag="PRIVATE_TAG"))
    user = service.get_user("p-b")
    before = service._db.total_changes
    review = preview(service, "delete", names=["p-a", "p-b", "p-c", "p-d"])
    proposal = review.proposal
    assert proposal is not None
    assert proposal.usernames == ["p-b", "p-d"]
    assert (proposal.fleet_id, proposal.revision, proposal.generation, proposal.plan_hash) == (
        service.fleet_id,
        revision.number,
        1,
        revision.plan_hash,
    )
    assert proposal.intent_sha256 == canonical_sha256(intent.model_dump(mode="json"))
    assert service._db.total_changes == before
    assert all(person.cli_command is None for person in review.people)
    assert all(value not in proposal.model_dump_json() for value in (user.uuid, user.sub_token, user.note, user.tag))
    calls = []
    compose = service.bulk.compose
    monkeypatch.setattr(service.bulk, "compose", lambda **kwargs: (calls.append(kwargs), compose(**kwargs))[-1])
    receipt = commit(service, review)
    assert receipt.proposal == proposal and service.bulk.receipt(receipt.id) == receipt
    assert receipt.counts.changed == receipt.counts.cli_required == 2
    assert len(calls) == 1 and {person.username for person in calls[0]["retired"]} == {"p-a", "p-c"}
    assert service.get_user("p-a") is None and service.get_user("p-c") is None
    assert service.get_user("p-b") is not None and service.get_user("p-d") is not None
    assert service.revisions.fence().record.revision == revision.number
    assert commit(service, review) == receipt


def test_new_committed_topology_refuses_the_entire_mixed_bulk_commit(committed_people):
    service, intent, _ = committed_people
    review = preview(service, "delete", names=["p-a", "p-b", "p-d"])
    commit_topology(service, intent, generation=2, plan_hash="c" * 64)
    before = service.events.list().to_data()
    with pytest.raises(ServiceError) as refused:
        commit(service, review)
    assert refused.value.code == "stale_topology"
    assert service.get_user("p-a") is not None
    assert service.events.list().to_data() == before
    assert service._db.execute("SELECT COUNT(*) FROM bulk_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["approved", "failed"])
def test_uncommitted_declared_people_need_a_finished_topology_before_a_removal_proposal(committed_people, status):
    from meridian.control.models import RevisionResult

    service, _, _ = committed_people
    service.create_user(UserCreate(username="p-pending"))
    revision = service.revisions.propose(
        RevisionProposal(intent={"access": {"users": ["p-pending"]}}, plan_hash="d" * 64, review_hash="e" * 64),
        caller=ROOT,
    )
    service.revisions.approve(revision.number, caller=ROOT)
    if status == "failed":
        service.revisions.record_result(revision.number, RevisionResult(status="failed"))
    with pytest.raises(ServiceError) as refused:
        preview(service, "delete", names=["p-a", "p-pending"])
    assert refused.value.code == "topology_uncommitted"
    assert service.get_user("p-a") is not None


def test_declared_proposal_needs_a_committed_service_fence_but_service_only_delete_does_not(people_service):
    seed(people_service)
    with pytest.raises(ServiceError) as refused:
        preview(people_service, "delete", names=["p-b"])
    assert refused.value.code == "topology_uncommitted"
    review = preview(people_service, "delete", names=["p-a"])
    assert review.proposal is None
    assert commit(people_service, review).counts.changed == 1
