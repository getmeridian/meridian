"""People queries share exact predicates, contextual counts, and stable pages."""

from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import RevisionProposal, RevisionResult, UserCreate, UserImport, UserImportBatch
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from tests.support.control_service import FLEET_ID

NOW = "2026-11-01T12:00:00Z"


@pytest.fixture
def people_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    service = ControlService(
        tmp_path / "people.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    yield service
    service.close()


def query(service: ControlService, **options):
    from meridian.core.people_query import PeopleQuery

    return service.people.query(PeopleQuery(**options))


def seed(service: ControlService) -> None:
    service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username="p-a",
                    uuid="00000000-0000-4000-8000-000000000001",
                    sub_token="sample-token-0001",
                    tag="GROUP_A",
                    expire_at="2026-11-03T12:00:00Z",
                    traffic_limit_bytes=100,
                    used_traffic_bytes=80,
                    lifetime_traffic_bytes=80,
                    created_at="2026-10-01T12:00:00Z",
                ),
                UserImport(
                    username="p-b",
                    uuid="00000000-0000-4000-8000-000000000002",
                    sub_token="sample-token-0002",
                    tag="GROUP_B",
                    status="disabled",
                    created_at="2026-10-01T12:00:00Z",
                ),
                UserImport(
                    username="p-c",
                    uuid="00000000-0000-4000-8000-000000000003",
                    sub_token="sample-token-0003",
                    tag="GROUP_A",
                    expire_at="2026-12-01T12:00:00Z",
                    traffic_limit_bytes=100,
                    used_traffic_bytes=79,
                    lifetime_traffic_bytes=79,
                ),
                UserImport(
                    username="p-d",
                    uuid="00000000-0000-4000-8000-000000000004",
                    sub_token="sample-token-0004",
                    activation_duration_days=30,
                ),
            ]
        )
    )
    revision = service.revisions.propose(
        RevisionProposal(
            intent={"access": {"users": ["p-b"], "display_names": {"p-b": "Sample label"}}},
            plan_hash="a" * 64,
            review_hash="b" * 64,
        ),
        caller=ROOT,
    )
    service.revisions.approve(revision.number, caller=ROOT)
    service.revisions.record_result(revision.number, RevisionResult(status="applied"))


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"q": "SAMPLE LABEL"}, ["p-b"]),
        ({"status": "active", "tag": "GROUP_A"}, ["p-a", "p-c"]),
        ({"expires": 7}, ["p-a"]),
        ({"quota": 80}, ["p-a"]),
        ({"never": True}, ["p-d", "p-b"]),
        ({"attention": "unused"}, ["p-b"]),
        ({"lifecycle": "pending"}, ["p-d"]),
    ],
)
def test_server_query_uses_existing_activity_and_schedule_evidence(people_service, filters, expected):
    seed(people_service)
    page = query(people_service, **filters)
    assert [user.username for user in page.users] == expected
    assert page.total == len(expected)
    assert all(not hasattr(user, "uuid") and not hasattr(user, "note") for user in page.users)


def test_contextual_facets_are_reads(people_service):
    seed(people_service)
    before = people_service._db.total_changes
    page = query(people_service, status="active")
    assert page.facets.tag["GROUP_A"] == 2 and page.facets.tag["GROUP_B"] == 0
    assert page.facets.status["disabled"] == 1
    assert page.facets.attention["unused"] == 0
    assert people_service._db.total_changes == before


@pytest.mark.parametrize(
    ("sort", "order"), [("name", "asc"), ("usage", "desc"), ("expiry", "desc"), ("activity", "asc")]
)
def test_cursor_uses_a_unique_order_and_excludes_insertions_after_the_first_page(people_service, sort, order):
    seed(people_service)
    first = query(people_service, sort=sort, order=order, limit=2)
    people_service.create_user(UserCreate(username="p-inserted"))
    second = query(people_service, sort=sort, order=order, limit=2, cursor=first.next_cursor)
    names = [user.username for user in [*first.users, *second.users]]
    assert len(names) == len(set(names)) == 4
    assert "p-inserted" not in names and second.next_cursor is None


def test_quota_share_does_not_round_across_a_large_integer_threshold(people_service):
    people_service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username="large",
                    uuid="00000000-0000-4000-8000-000000000005",
                    sub_token="sample-token-0005",
                    traffic_limit_bytes=9007199254740991,
                    used_traffic_bytes=7205759403792792,
                    lifetime_traffic_bytes=7205759403792792,
                )
            ]
        )
    )
    assert query(people_service, quota=80).total == 0


def test_cursor_refuses_another_query_and_a_tampered_token(people_service):
    seed(people_service)
    cursor = query(people_service, limit=1).next_cursor
    from meridian.control.errors import ServiceError

    for options in ({"cursor": cursor, "status": "active"}, {"cursor": cursor + "x"}):
        with pytest.raises(ServiceError, match="cursor"):
            query(people_service, **options)
