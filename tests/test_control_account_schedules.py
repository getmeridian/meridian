"""Account schedules use accepted traffic and catch up without losing new traffic."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.control.models import UserCreate, UserImport, UserImportBatch, UserUpdate
from meridian.control.service import ControlService
from meridian.core.agent_protocol import StatsReport, UserTraffic
from meridian.migrate.users import carry_users
from meridian.remnawave import User
from tests.support.control_service import FLEET_ID

EPOCH = "6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def scheduled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ControlService, list[str]]]:
    clock = ["2027-01-31T12:00:00Z"]
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: clock[0])
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(clock[0]).astimezone(UTC).timestamp(),
    )
    try:
        yield service, clock
    finally:
        service.close()


def count(service: ControlService, clock: list[str], total: int, seq: int = 1, *, upload: int = 0) -> None:
    person = service.get_user("example")
    assert person is not None
    service.record_stats(
        "main",
        StatsReport(
            counter_epoch=EPOCH,
            seq=seq,
            collected_at=clock[0],
            users=[UserTraffic(user_id=person.user_id, uplink_bytes=upload, downlink_bytes=total)],
        ),
    )


def test_anniversary_reset_keeps_creation_day_after_short_months_and_downtime(scheduled) -> None:
    service, clock = scheduled
    service.create_user(UserCreate(username="example", traffic_limit_strategy="month_rolling"))
    clock[0] = "2027-02-01T12:00:00Z"
    count(service, clock, 80)
    person = service.get_user("example")
    assert person.traffic_reset_anchor_day == 31 and person.used_traffic_bytes == 80
    assert person.schedule.next_reset_at == "2027-02-28T00:00:00Z"
    clock[0] = "2027-02-28T00:00:00Z"
    service.enforce()
    assert service.get_user("example").used_traffic_bytes == 0
    assert service.get_user("example").schedule.next_reset_at == "2027-03-31T00:00:00Z"
    clock[0] = "2027-06-04T10:00:00Z"
    count(service, clock, 100, seq=2)
    service.enforce()
    assert service.get_user("example").used_traffic_bytes == 20
    assert service.get_user("example").schedule.next_reset_at == "2027-06-30T00:00:00Z"


@pytest.mark.parametrize(
    ("settings", "boundary", "renewals"),
    [
        (
            {"traffic_limit_strategy": "month_rolling", "traffic_reset_anchor_day": 31},
            "2027-02-28T00:00:00Z",
            0,
        ),
        (
            {"expire_at": "2027-02-01T00:00:00Z", "renewal_interval_days": 7, "renewal_limit": 4},
            "2027-02-17T00:00:00Z",
            3,
        ),
        (
            {
                "traffic_limit_strategy": "month_rolling",
                "traffic_reset_anchor_day": 31,
                "expire_at": "2027-02-28T00:00:00Z",
                "renewal_interval_days": 7,
                "renewal_limit": 4,
            },
            "2027-02-28T00:00:00Z",
            1,
        ),
    ],
    ids=["rolling", "renewal-catch-up", "rolling-and-renewal"],
)
def test_schedule_resets_both_directions_and_carried_use_before_counting_growth(
    scheduled, settings: dict, boundary: str, renewals: int
) -> None:
    service, clock = scheduled
    service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username="example",
                    uuid="00000000-0000-4000-8000-000000000001",
                    sub_token="exampleToken0001",
                    used_traffic_bytes=30,
                    lifetime_traffic_bytes=100,
                    **settings,
                )
            ]
        )
    )
    count(service, clock, 90, upload=40)
    person = service.get_user("example")
    assert person.period_traffic.to_data() == {
        "upload_bytes": 40,
        "download_bytes": 120,
        "unattributed_bytes": 30,
    }
    assert person.lifetime_traffic_bytes == 230
    clock[0] = boundary
    count(service, clock, 103, seq=2, upload=47)
    count(service, clock, 103, seq=2, upload=47)
    service.enforce()
    person = service.get_user("example")
    assert person.period_traffic.to_data() == {
        "upload_bytes": 7,
        "download_bytes": 13,
        "unattributed_bytes": 0,
    }
    assert person.used_traffic_bytes == 20 and person.lifetime_traffic_bytes == 250
    assert person.renewals_completed == renewals
    assert service.subscription(person.sub_token).headers["subscription-userinfo"].startswith("upload=7; download=13;")


@pytest.mark.parametrize(
    ("created", "anchor", "now", "next_reset"),
    [
        ("2028-01-31T10:00:00Z", None, "2028-02-01T00:00:00Z", "2028-02-29T00:00:00Z"),
        ("2027-01-31T10:00:00Z", 15, "2027-02-14T23:59:59Z", "2027-02-15T00:00:00Z"),
        ("2027-01-31T10:00:00Z", 31, "2027-03-14T23:00:00-04:00", "2027-03-31T00:00:00Z"),
    ],
)
def test_anniversary_refill_header_and_account_preview_share_the_same_utc_date(
    scheduled, created: str, anchor: int | None, now: str, next_reset: str
) -> None:
    service, clock = scheduled
    clock[0] = created
    user = service.create_user(
        UserCreate(
            username="example",
            traffic_limit_strategy="month_rolling",
            traffic_reset_anchor_day=anchor,
        )
    )
    clock[0] = now
    response = service.subscription(user.sub_token)
    assert response.headers["subscription-refill-date"] == str(int(datetime.fromisoformat(next_reset).timestamp()))
    assert service.get_user("example").schedule.next_reset_at == next_reset


def test_opening_subscription_or_page_and_zero_stats_never_activate_a_duration(scheduled) -> None:
    service, clock = scheduled
    person = service.create_user(UserCreate(username="example", activation_duration_days=30))
    service.subscription(person.sub_token)
    service.subscription(person.sub_token, suffix="info")
    service.subscription(person.sub_token, accept="text/html")
    count(service, clock, 0)
    person = service.get_user("example")
    assert person.first_traffic_at is None and person.expire_at is None
    assert person.schedule.activation_pending and person.activation_duration_days == 30
    clock[0] = "2027-03-10T23:30:00Z"
    count(service, clock, 1, seq=2)
    person = service.get_user("example")
    assert person.first_traffic_at == clock[0] and person.expire_at == "2027-04-09T23:30:00Z"
    assert not person.schedule.activation_pending
    clock[0] = "2027-03-20T23:30:00Z"
    count(service, clock, 9, seq=3)
    assert service.get_user("example").expire_at == "2027-04-09T23:30:00Z"


def test_extending_an_unused_account_keeps_its_duration_pending_and_renewal_waits(scheduled) -> None:
    service, clock = scheduled
    service.create_user(
        UserCreate(username="example", activation_duration_days=30, renewal_interval_days=7, renewal_limit=2)
    )
    clock[0] = "2027-06-01T00:00:00Z"
    person = service.extend_user("example", days=7)
    assert (person.activation_duration_days, person.expire_at) == (37, None)
    assert person.schedule.next_renewal_at is None and person.schedule.renewals_left == 2
    count(service, clock, 1)
    assert service.get_user("example").expire_at == "2027-07-08T00:00:00Z"


@pytest.mark.parametrize("disabled", [False, True])
def test_missed_renewals_reset_traffic_once_and_preserve_operator_disable(scheduled, disabled: bool) -> None:
    service, clock = scheduled
    service.create_user(
        UserCreate(
            username="example",
            expire_at="2027-02-01T12:00:00Z",
            traffic_limit_bytes=10,
            renewal_interval_days=7,
            renewal_limit=4,
        )
    )
    count(service, clock, 10)
    assert service.get_user("example").status == "limited"
    if disabled:
        service.update_user("example", UserUpdate(status="disabled"))
    clock[0] = "2027-02-17T12:00:00Z"
    count(service, clock, 13, seq=2)
    person = service.get_user("example")
    assert (person.expire_at, person.renewals_completed) == ("2027-02-22T12:00:00Z", 3)
    assert person.used_traffic_bytes == 3 and person.lifetime_traffic_bytes == 13
    assert person.status == ("disabled" if disabled else "active")
    assert person.schedule.next_renewal_at == "2027-02-22T12:00:00Z" and person.schedule.renewals_left == 1
    service.enforce()
    assert service.get_user("example").renewals_completed == 3
    clock[0] = "2027-03-08T12:00:00Z"
    service.enforce()
    person = service.get_user("example")
    assert (person.expire_at, person.renewals_completed, person.schedule.renewals_left) == (
        "2027-03-01T12:00:00Z",
        4,
        0,
    )
    assert person.status == ("disabled" if disabled else "expired") and person.schedule.next_renewal_at is None


@pytest.mark.parametrize(
    ("expiry", "now", "renewed", "count_due"),
    [
        ("2027-01-31T23:30:00Z", "2027-04-01T00:00:00Z", "2027-04-30T23:30:00Z", 3),
        ("2028-01-31T23:30:00Z", "2028-02-29T23:30:00Z", "2028-03-31T23:30:00Z", 2),
        ("2027-10-31T01:30:00Z", "2027-12-01T00:00:00Z", "2027-12-31T01:30:00Z", 2),
    ],
)
def test_calendar_renewal_clamps_without_permanently_losing_the_original_day(
    scheduled, expiry: str, now: str, renewed: str, count_due: int
) -> None:
    service, clock = scheduled
    clock[0] = "2027-01-01T00:00:00Z"
    service.create_user(UserCreate(username="example", expire_at=expiry, renewal_interval_months=1))
    clock[0] = now
    service.enforce()
    person = service.get_user("example")
    assert person.expire_at == renewed and person.renewals_completed == count_due
    assert person.renewal_anchor_at == expiry and person.schedule.renewals_left is None


def test_renewal_and_reset_roll_back_together_before_retry(scheduled, monkeypatch: pytest.MonkeyPatch) -> None:
    service, clock = scheduled
    service.create_user(UserCreate(username="example", expire_at="2027-02-01T00:00:00Z", renewal_interval_days=7))
    count(service, clock, 10)
    clock[0] = "2027-02-08T00:00:00Z"
    append = service.events.append

    def refuse_reset(kind, *args, **kwargs):
        if kind == "user.traffic_reset":
            raise RuntimeError("test interruption")
        return append(kind, *args, **kwargs)

    monkeypatch.setattr(service.events, "append", refuse_reset)
    with pytest.raises(RuntimeError, match="test interruption"):
        service.enforce()
    assert (service.get_user("example").renewals_completed, service.get_user("example").used_traffic_bytes) == (0, 10)
    monkeypatch.setattr(service.events, "append", append)
    service.enforce()
    assert (service.get_user("example").renewals_completed, service.get_user("example").used_traffic_bytes) == (2, 0)


def test_rolling_migration_keeps_the_original_creation_anchor_without_a_loss_report() -> None:
    person = User(
        username="example",
        vless_uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f",
        short_uuid="exampleToken0001",
        status="ACTIVE",
        traffic_limit_strategy="MONTH_ROLLING",
        created_at="2027-01-31T12:00:00.000Z",
    )
    imported = carry_users([person], ["example"])
    assert imported.batch.users[0].traffic_limit_strategy == "month_rolling"
    assert imported.batch.users[0].traffic_reset_anchor_day == 31 and imported.notes == {}


def test_import_preserves_activated_duration_renewal_progress_and_device_exemption(scheduled) -> None:
    service, _clock = scheduled
    item = UserImport(
        username="example",
        uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f",
        sub_token="exampleToken0001",
        expire_at="2027-03-31T00:00:00Z",
        activation_duration_days=30,
        first_traffic_at="2027-01-01T00:00:00Z",
        renewal_interval_months=1,
        renewal_limit=5,
        renewals_completed=2,
        renewal_anchor_at="2027-01-31T00:00:00Z",
        traffic_limit_strategy="month_rolling",
        traffic_reset_anchor_day=31,
        device_limit=0,
        used_traffic_bytes=20,
        lifetime_traffic_bytes=30,
    )
    service.import_users(UserImportBatch(users=[item]))
    person = service.get_user("example")
    for field in (
        "expire_at",
        "activation_duration_days",
        "first_traffic_at",
        "renewal_interval_months",
        "renewal_limit",
        "renewals_completed",
        "renewal_anchor_at",
        "traffic_reset_anchor_day",
        "device_limit",
    ):
        assert getattr(person, field) == getattr(item, field)
    assert service.put_user("example").renewals_completed == 2


def test_replacing_an_import_keeps_the_language_the_source_does_not_own(scheduled) -> None:
    service, _clock = scheduled
    item = UserImport(username="example", uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f", sub_token="exampleToken0001")
    service.import_users(UserImportBatch(users=[item]))
    service.update_user("example", UserUpdate(language="ru"))
    service.import_users(UserImportBatch(users=[item], replace=True))
    assert service.get_user("example").language == "ru"
