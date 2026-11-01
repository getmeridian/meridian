"""Undo writes operator choices before lifecycle enforcement and checks inbound capacity."""

import pytest

from meridian.control.errors import ServiceError
from meridian.control.models import UserImport, UserImportBatch, UserUpdate
from meridian.core.agent_desired import MAX_USERS_PER_INBOUND
from tests.support.node_commands import command_node_spec
from tests.test_control_people_bulk import commit, preview
from tests.test_control_people_query import people_service as people_service
from tests.test_control_people_query import seed


@pytest.mark.parametrize(
    ("initial_limit", "bulk_limit", "kind", "forbidden"),
    [(100, 50, "user.restored", "user.enabled"), (50, 100, "user.limited", "user.enabled")],
)
def test_undo_quota_uses_enforcement_events_without_an_operator_status_edit(
    people_service, initial_limit, bulk_limit, kind, forbidden
):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(traffic_limit_bytes=initial_limit))
    receipt = commit(people_service, preview(people_service, "quota", names=["p-a"], traffic_limit_bytes=bulk_limit))
    cursor = people_service.events.list().next_cursor
    undone = people_service.bulk.undo(receipt.id)
    events = people_service.events.list(after=cursor).events
    kinds = [event.kind for event in events]
    assert kind in kinds and forbidden not in kinds
    edited = next(event for event in events if event.kind == "user.updated")
    assert edited.detail.fields == ["traffic_limit_bytes"]
    enforced = next(event for event in events if event.kind == kind)
    assert enforced.actor == "system"
    assert undone.undo_people[0].after == receipt.people[0].before


@pytest.mark.parametrize(
    ("limits", "status"),
    [({"expire_at": "2026-10-31T12:00:00Z"}, "expired"), ({"traffic_limit_bytes": 50}, "limited")],
)
def test_undo_disable_requests_enable_then_applies_the_remaining_limit(people_service, limits, status):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(**limits))
    receipt = commit(people_service, preview(people_service, "disable", names=["p-a"]))
    cursor = people_service.events.list().next_cursor
    undone = people_service.bulk.undo(receipt.id)
    events = people_service.events.list(after=cursor).events
    kinds = [event.kind for event in events]
    assert kinds == ["user.enabled", "user." + status, "people.bulk"]
    assert events[1].actor == "system"
    assert undone.undo_people[0].after.status == status


def test_undo_above_the_managed_inbound_limit_is_a_service_refusal_and_rolls_back(people_service):
    seed(people_service)
    people_service.put_node_spec("main", command_node_spec())
    receipt = commit(people_service, preview(people_service, "disable", names=["p-a"]))
    active = sum(user.status == "active" for user in people_service.list_users())
    people_service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username=f"p-cap-{index:05d}",
                    uuid=f"00000000-0000-4000-8000-{index + 100:012d}",
                    sub_token=f"sample-cap-token-{index:012d}",
                )
                for index in range(MAX_USERS_PER_INBOUND - active)
            ]
        )
    )
    revision = people_service.node_status("main").revision
    cursor = people_service._db.execute("SELECT MAX(id) FROM events").fetchone()[0]
    with pytest.raises(ServiceError) as caught:
        people_service.bulk.undo(receipt.id)
    assert caught.value.code == "invalid_request" and caught.value.status == 400
    assert str(MAX_USERS_PER_INBOUND) in str(caught.value)
    assert people_service.get_user("p-a").status == "disabled"
    assert people_service.node_status("main").revision == revision
    assert people_service.events.list(after=cursor).events == []
    assert people_service.bulk.receipt(receipt.id).undone_at is None


def test_successful_undo_composes_the_batch_once(people_service, monkeypatch):
    seed(people_service)
    people_service.put_node_spec("main", command_node_spec())
    receipt = commit(people_service, preview(people_service, "disable", names=["p-a", "p-c"]))
    revision = people_service.node_status("main").revision
    calls = []
    compose = people_service.bulk.compose
    monkeypatch.setattr(people_service.bulk, "compose", lambda: (calls.append(1), compose())[-1])
    people_service.bulk.undo(receipt.id)
    assert calls == [1]
    assert people_service.node_status("main").revision == revision + 1
