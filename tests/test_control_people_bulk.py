"""Bulk review freezes rows, rolls back the whole batch, and refuses unsafe undo."""

import json
from datetime import datetime

import pytest

from meridian.control.errors import ServiceError
from meridian.control.models import UserCreate, UserUpdate
from meridian.core.agent_protocol import StatsReport, UserTraffic
from tests.test_control_people_query import NOW, seed
from tests.test_control_people_query import people_service as people_service


def preview(service, action="disable", *, names=None, filters=None, **params):
    from meridian.core.people_bulk import BulkAction, BulkPreviewRequest
    from meridian.core.people_query import PeopleQuery

    return service.bulk.preview(
        BulkPreviewRequest(
            action=BulkAction(action=action, **params),
            usernames=names,
            query=PeopleQuery(**(filters or {})) if names is None else None,
        )
    )


def commit(service, review):
    from meridian.core.people_bulk import BulkCommitRequest

    return service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))


def test_bulk_preview_changes_no_table_and_freezes_all_matching(people_service):
    seed(people_service)
    before = people_service._db.total_changes
    review = preview(people_service, filters={"tag": "GROUP_A"})
    assert [person.username for person in review.people] == ["p-a", "p-c"]
    assert all(person.before.status == "active" and person.after.status == "disabled" for person in review.people)
    assert people_service._db.total_changes == before
    people_service.create_user(UserCreate(username="p-added"))
    people_service.update_user("p-added", UserUpdate(tag="GROUP_A"))
    receipt = commit(people_service, review)
    assert receipt.counts.changed == 2
    assert people_service.get_user("p-added").status == "active"
    assert commit(people_service, review) == receipt


def test_overlapping_reviews_cannot_both_commit_even_after_a_field_is_changed_back(people_service):
    seed(people_service)
    first = preview(people_service, names=["p-a", "p-c"])
    second = preview(people_service, names=["p-a"])
    commit(people_service, first)
    people_service.update_user("p-a", UserUpdate(status="active"))
    with pytest.raises(ServiceError, match="p-a"):
        commit(people_service, second)


def test_any_failure_rolls_back_people_events_and_receipt(people_service, monkeypatch):
    seed(people_service)
    review = preview(people_service, names=["p-a", "p-c"])
    before = people_service.events.list().model_dump()
    monkeypatch.setattr(
        people_service.bulk, "compose", lambda: (_ for _ in ()).throw(RuntimeError("composition failed"))
    )
    with pytest.raises(RuntimeError):
        commit(people_service, review)
    assert [people_service.get_user(name).status for name in ("p-a", "p-c")] == ["active", "active"]
    assert people_service.events.list().model_dump() == before
    assert people_service._db.execute("SELECT COUNT(*) FROM bulk_receipts").fetchone()[0] == 0


def test_undo_survives_reopening_but_refuses_a_later_edit(people_service):
    seed(people_service)
    review = preview(people_service, "tag", names=["p-a"], tag="REVIEWED")
    receipt = commit(people_service, review)
    assert receipt.undo_until is not None
    people_service.update_user("p-a", UserUpdate(note="Private sample note"))
    with pytest.raises(ServiceError, match="p-a"):
        people_service.bulk.undo(receipt.id)
    assert people_service.get_user("p-a").tag == "REVIEWED"


def test_undo_restores_fields_and_is_single_use(people_service):
    seed(people_service)
    receipt = commit(people_service, preview(people_service, "devices", names=["p-a", "p-b"], device_limit=0))
    undone = people_service.bulk.undo(receipt.id)
    assert undone.undone_at is not None
    assert people_service.get_user("p-a").device_limit is None
    assert people_service.get_user("p-b").device_limit is None
    with pytest.raises(ServiceError, match="already"):
        people_service.bulk.undo(receipt.id)


def test_delete_keeps_declared_people_and_returns_the_exact_reviewed_cli_command(people_service):
    seed(people_service)
    review = preview(people_service, "delete", names=["p-a", "p-b"])
    assert not review.reversible
    declared = next(person for person in review.people if person.username == "p-b")
    assert declared.cli_command == "meridian client remove p-b"
    receipt = commit(people_service, review)
    assert receipt.counts.changed == 1 and receipt.counts.cli_required == 1
    assert people_service.get_user("p-a") is None and people_service.get_user("p-b") is not None
    with pytest.raises(ServiceError, match="cannot be undone"):
        people_service.bulk.undo(receipt.id)


def test_traffic_or_enforcement_racing_commit_refuses_every_target(people_service):
    seed(people_service)
    review = preview(people_service, "quota", names=["p-a", "p-c"], traffic_limit_bytes=200)
    user = people_service.get_user("p-a")
    people_service.record_stats(
        "main",
        StatsReport(
            counter_epoch="00000000-0000-4000-8000-000000000006",
            seq=1,
            collected_at=NOW,
            users=[UserTraffic(user_id=user.user_id, uplink_bytes=1, downlink_bytes=0)],
        ),
        now=NOW,
    )
    with pytest.raises(ServiceError, match="p-a"):
        commit(people_service, review)
    assert people_service.get_user("p-c").traffic_limit_bytes == 100


@pytest.mark.parametrize(("params", "days"), [({"days": 7}, 37), ({"months": 1}, 60)])
def test_pending_extension_extends_duration_without_starting_first_use(people_service, params, days):
    seed(people_service)
    receipt = commit(people_service, preview(people_service, "extend", names=["p-d"], **params))
    assert receipt.people[0].after.activation_duration_days == days
    assert people_service.get_user("p-d").expire_at is None


def test_receipts_and_bulk_events_never_contain_notes_or_credentials_or_event_tags(people_service):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(note="Private sample note"))
    user = people_service.get_user("p-a")
    receipt = commit(people_service, preview(people_service, "tag", names=["p-a"], tag="REVIEWED"))
    events = people_service.events.list()
    encoded = json.dumps(receipt.model_dump(mode="json"))
    assert all(secret not in encoded for secret in (user.uuid, user.sub_token, user.note))
    assert "REVIEWED" not in json.dumps(events.model_dump(mode="json"))
    assert len([event for event in events.events if event.kind == "people.bulk"]) == 1


def test_quota_reactivation_uses_the_existing_restored_event(people_service):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(traffic_limit_bytes=50))
    cursor = people_service.events.list().next_cursor
    commit(people_service, preview(people_service, "quota", names=["p-a"], traffic_limit_bytes=100))
    kinds = [event.kind for event in people_service.events.list(after=cursor).events]
    assert "user.restored" in kinds and "user.enabled" not in kinds


def test_renewal_cap_only_preserves_the_existing_interval_and_progress(people_service):
    seed(people_service)
    before = people_service.update_user("p-a", UserUpdate(renewal_interval_months=1, renewal_limit=10))
    commit(people_service, preview(people_service, "renewal", names=["p-a"], renewal_limit=5))
    after = people_service.get_user("p-a")
    assert after.renewal_interval_months == 1 and after.renewal_limit == 5
    assert (after.renewals_completed, after.renewal_anchor_at) == (before.renewals_completed, before.renewal_anchor_at)


def test_undo_refuses_a_clock_boundary_that_changes_the_reviewed_status(people_service):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(expire_at="2026-11-01T12:01:00Z"))
    receipt = commit(people_service, preview(people_service, "disable", names=["p-a"]))
    people_service.bulk.clock = lambda: datetime.fromisoformat("2026-11-01T12:01:01Z").timestamp()
    with pytest.raises(ServiceError, match="p-a"):
        people_service.bulk.undo(receipt.id)
    assert people_service.get_user("p-a").status == "disabled"
