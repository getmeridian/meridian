"""Bulk reviews compare enforced account states at each boundary without losing resets."""

from datetime import datetime

import pytest

from meridian.control.errors import ServiceError
from meridian.control.models import UserUpdate
from meridian.control.period_usage import import_period
from tests.test_control_people_bulk import commit, preview
from tests.test_control_people_query import people_service as people_service
from tests.test_control_people_query import seed


def advance(service, moment):
    service.bulk.clock = lambda: datetime.fromisoformat(moment).timestamp()


@pytest.mark.parametrize("renewing", [False, True], ids=["expiry", "renewal"])
@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("extend", {"months": 1}),
        ("expiry", {"expire_at": "2026-12-01T12:00:05Z"}),
        ("disable", {}),
        ("delete", {}),
        ("renewal", {"renewal_interval_days": None, "renewal_interval_months": None}),
    ],
)
def test_commit_refuses_every_target_when_its_enforced_before_crosses_a_boundary(
    people_service, action, params, renewing
):
    seed(people_service)
    people_service.update_user(
        "p-a",
        UserUpdate(expire_at="2026-11-01T12:00:05Z", renewal_interval_months=1 if renewing else None),
    )
    review = preview(people_service, action, names=["p-c", "p-a"], **params)
    snapshot = list(people_service._db.iterdump())
    advance(people_service, "2026-11-01T12:00:10Z")
    with pytest.raises(ServiceError) as caught:
        commit(people_service, review)
    assert caught.value.code == "stale_people"
    assert caught.value.subjects == ["p-a"]
    assert list(people_service._db.iterdump()) == snapshot


def test_a_noop_preview_starts_from_the_enforced_status_and_stays_read_only(people_service):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(expire_at="2026-11-01T12:00:05Z"))
    advance(people_service, "2026-11-01T12:00:10Z")
    changes = people_service._db.total_changes
    review = preview(people_service, "tag", names=["p-a"], tag="GROUP_A")
    assert review.people[0].before.status == review.people[0].after.status == "expired"
    assert review.people[0].outcome == "unchanged" and review.people[0].changes == []
    assert review.counts.changed == 0 and review.counts.unchanged == 1
    assert people_service._db.total_changes == changes
    advance(people_service, "2026-11-01T12:00:11Z")
    receipt = commit(people_service, review)
    assert receipt.counts.unchanged == 1 and receipt.undo_until is None
    assert people_service.get_user("p-a").status == "expired"


def test_metadata_undo_keeps_the_expiry_already_due_before_review(people_service):
    seed(people_service)
    people_service.update_user("p-a", UserUpdate(expire_at="2026-11-01T12:00:05Z"))
    advance(people_service, "2026-11-01T12:00:10Z")
    receipt = commit(people_service, preview(people_service, "tag", names=["p-a"], tag="REVIEWED"))
    assert receipt.people[0].before.status == "expired"
    undone = people_service.bulk.undo(receipt.id)
    assert undone.undo_people[0].after.status == "expired"
    assert people_service.get_user("p-a").tag == "GROUP_A"


@pytest.mark.parametrize("boundary", ["renewal", "period"])
def test_review_and_commit_preserve_the_reset_already_due_before_the_action(people_service, boundary):
    seed(people_service)
    if boundary == "renewal":
        people_service.update_user("p-a", UserUpdate(expire_at="2026-11-01T12:00:05Z", renewal_interval_months=1))
        advance(people_service, "2026-11-01T12:00:10Z")
    else:
        people_service.update_user("p-a", UserUpdate(traffic_limit_strategy="day"))
        import_period(people_service._store, "p-a", 80)
        people_service._db.execute("UPDATE users SET traffic_reset_at = '2026-10-31T12:00:00Z' WHERE username = 'p-a'")
    cursor = people_service.events.list().next_cursor
    changes = people_service._db.total_changes
    review = preview(people_service, "tag", names=["p-a"], tag="REVIEWED")
    assert review.people[0].before.used_traffic_bytes == review.people[0].after.used_traffic_bytes == 0
    assert review.people[0].changes == ["tag"] and review.reversible
    assert people_service._db.total_changes == changes
    receipt = commit(people_service, review)
    actual = people_service.get_user("p-a")
    assert actual.used_traffic_bytes == 0 and actual.lifetime_traffic_bytes == 80
    assert actual.period_traffic.upload_bytes == actual.period_traffic.download_bytes == 0
    resets = [event for event in people_service.events.list(after=cursor).events if event.kind == "user.traffic_reset"]
    assert len(resets) == 1 and resets[0].actor == "system"
    undone = people_service.bulk.undo(receipt.id)
    assert undone.undo_people[0].after == receipt.people[0].before
    assert people_service.get_user("p-a").used_traffic_bytes == 0


def test_manual_bulk_reset_records_the_commit_time(people_service):
    seed(people_service)
    review = preview(people_service, "reset-traffic", names=["p-a"])
    advance(people_service, "2026-11-01T12:00:10Z")
    receipt = commit(people_service, review)
    assert receipt.people[0].after.traffic_reset_at == receipt.committed_at == "2026-11-01T12:00:10Z"
    assert people_service.get_user("p-a").traffic_reset_at == receipt.committed_at


@pytest.mark.parametrize("action", ["expiry", "renewal"])
def test_commit_also_refuses_when_the_reviewed_after_crosses_a_schedule_boundary(people_service, action):
    from meridian.control.bulk_effects import project_effect
    from meridian.core.people_bulk import BulkFields

    seed(people_service)
    params = {"expire_at": "2026-11-01T12:00:05Z"}
    if action == "renewal":
        people_service.update_user("p-a", UserUpdate(expire_at="2026-10-31T12:00:05Z"))
        params = {"renewal_interval_days": 1}
    review = preview(people_service, action, names=["p-a"], **params)
    snapshot = list(people_service._db.iterdump())
    moment = "2026-11-01T12:00:10Z"
    advance(people_service, moment)
    current = project_effect(people_service.get_user("p-a"), moment).user
    assert BulkFields.from_user(current) == review.people[0].before
    with pytest.raises(ServiceError) as caught:
        commit(people_service, review)
    assert caught.value.code == "stale_people" and caught.value.subjects == ["p-a"]
    assert list(people_service._db.iterdump()) == snapshot
    refreshed = commit(people_service, preview(people_service, action, names=["p-a"], **params))
    if action == "renewal":
        assert refreshed.people[0].after.renewals_completed == 2
        assert people_service.get_user("p-a").used_traffic_bytes == 0
        assert people_service.get_user("p-a").lifetime_traffic_bytes == 80
