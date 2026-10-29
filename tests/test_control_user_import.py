"""Importing users into the control service (`meridian migrate`): credentials, status, limits, and usage carry over."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.control.client import ControlClient, ControlServiceConflict
from meridian.control.models import UserImport, UserImportBatch
from meridian.control.service import ControlService, ServiceError, period_start
from meridian.core.agent_protocol import StatsReport, UserTraffic
from tests.support.control_service import ADMIN_TOKEN, serve_control

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ALICE_UUID = "3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f"
BOB_UUID = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
# Remnawave's default short UUID: nanoid over its alphabet, 16 characters.
ALICE_SHORT = "Zq8xT3vNw2LmK7pR"
BOB_SHORT = "hX4_n-Q9rT2wYb7K"
GB = 10**9
EPOCH = "6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    return ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")


def _alice(**changes: object) -> UserImport:
    return UserImport.model_validate({"username": "alice", "uuid": ALICE_UUID, "sub_token": ALICE_SHORT, **changes})


def _bob(**changes: object) -> UserImport:
    return UserImport.model_validate({"username": "bob", "uuid": BOB_UUID, "sub_token": BOB_SHORT, **changes})


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_imported_user_keeps_their_link_credentials(service: ControlService) -> None:
    result = service.import_users(UserImportBatch(users=[_alice(), _bob(status="disabled")]))

    assert (result.created, result.unchanged) == (["alice", "bob"], [])
    alice = service.get_user("alice")
    assert alice is not None and (alice.uuid, alice.sub_token, alice.status) == (ALICE_UUID, ALICE_SHORT, "active")
    assert service.get_user("bob").status == "disabled"  # type: ignore[union-attr]
    assert service.subscription(ALICE_SHORT).status == 200


def test_an_uppercase_uuid_stays_the_hysteria2_password_it_was(service: ControlService) -> None:
    service.import_users(UserImportBatch(users=[_alice(uuid=ALICE_UUID.upper())]))

    assert service.get_user("alice").uuid == ALICE_UUID.upper()  # type: ignore[union-attr]
    with pytest.raises(ServiceError) as error:
        service.import_users(UserImportBatch(users=[_bob(uuid=ALICE_UUID)]))
    assert error.value.status == 409


def test_imported_use_counts_toward_the_limit(service: ControlService) -> None:
    imported = _alice(traffic_limit_bytes=10 * GB, used_traffic_bytes=10 * GB, lifetime_traffic_bytes=40 * GB)
    service.import_users(UserImportBatch(users=[imported]))

    alice = service.get_user("alice")
    assert alice is not None
    assert (alice.status, alice.used_traffic_bytes, alice.lifetime_traffic_bytes) == ("limited", 10 * GB, 40 * GB)


def test_agent_traffic_adds_to_the_imported_use_and_a_reset_keeps_the_lifetime(service: ControlService) -> None:
    imported = _alice(traffic_limit_bytes=10 * GB, used_traffic_bytes=3 * GB, lifetime_traffic_bytes=5 * GB)
    service.import_users(UserImportBatch(users=[imported]))
    alice = service.get_user("alice")
    assert alice is not None
    service.traffic.record(
        "main",
        StatsReport(
            counter_epoch=EPOCH,
            seq=1,
            collected_at="2026-10-29T12:00:00Z",
            users=[UserTraffic(user_id=alice.user_id, uplink_bytes=GB, downlink_bytes=GB)],
        ),
    )

    counted = service.get_user("alice")
    reset = service.reset_user_traffic("alice")

    assert counted is not None and (counted.used_traffic_bytes, counted.lifetime_traffic_bytes) == (5 * GB, 7 * GB)
    assert (reset.used_traffic_bytes, reset.lifetime_traffic_bytes) == (0, 7 * GB)


def test_a_lifetime_below_the_period_use_is_raised_to_it(service: ControlService) -> None:
    service.import_users(UserImportBatch(users=[_alice(used_traffic_bytes=2 * GB, lifetime_traffic_bytes=0)]))

    alice = service.get_user("alice")
    assert alice is not None and (alice.used_traffic_bytes, alice.lifetime_traffic_bytes) == (2 * GB, 2 * GB)


def test_a_reset_inside_the_current_period_is_kept_and_an_older_one_never_resets_the_imported_use(
    service: ControlService,
) -> None:
    start = period_start("month", datetime.now(UTC))
    assert start is not None
    inside, before = _iso(start), _iso(start - timedelta(days=3))
    service.import_users(
        UserImportBatch(
            users=[
                _alice(traffic_limit_strategy="month", used_traffic_bytes=GB, traffic_reset_at=inside),
                _bob(traffic_limit_strategy="month", used_traffic_bytes=GB, traffic_reset_at=before),
            ]
        )
    )
    service.enforce()

    alice, bob = service.get_user("alice"), service.get_user("bob")
    assert alice is not None and bob is not None
    assert alice.traffic_reset_at == inside and bob.traffic_reset_at != before
    assert (alice.used_traffic_bytes, bob.used_traffic_bytes) == (GB, GB)


def test_an_imported_expiry_in_the_past_expires_the_user(service: ControlService) -> None:
    service.import_users(UserImportBatch(users=[_alice(expire_at="2026-01-01T00:00:00.000Z")]))

    alice = service.get_user("alice")
    assert alice is not None and (alice.status, alice.expire_at) == ("expired", "2026-01-01T00:00:00Z")


def test_importing_the_same_users_again_changes_nothing(service: ControlService) -> None:
    service.import_users(UserImportBatch(users=[_alice(used_traffic_bytes=GB)]))
    service.reset_user_traffic("alice")

    again = service.import_users(UserImportBatch(users=[_alice(used_traffic_bytes=GB), _bob()]))

    assert (again.created, again.unchanged) == (["bob"], ["alice"])
    assert service.get_user("alice").used_traffic_bytes == 0  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "clash",
    [
        _alice(uuid=BOB_UUID, sub_token=BOB_SHORT),
        _bob(uuid=ALICE_UUID),
        _bob(sub_token=ALICE_SHORT),
    ],
    ids=["same-name-other-credentials", "uuid-held-by-another", "token-held-by-another"],
)
def test_a_disagreeing_duplicate_refuses_the_whole_batch(service: ControlService, clash: UserImport) -> None:
    service.import_users(UserImportBatch(users=[_alice()]))
    carol = UserImport(username="carol", uuid="11111111-2222-4333-8444-555555555555", sub_token="CarolShortUuid001")

    with pytest.raises(ServiceError) as error:
        service.import_users(UserImportBatch(users=[carol, clash]))

    assert error.value.status == 409
    assert service.get_user("carol") is None


def test_a_batch_naming_one_user_twice_is_invalid() -> None:
    with pytest.raises(ValidationError, match="share a username"):
        UserImportBatch(users=[_alice(), _alice(uuid=BOB_UUID, sub_token=BOB_SHORT)])


@pytest.mark.parametrize(
    "token",
    [
        "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ_abcdefghjkmnopqrstuvwxyz-0123",
        "3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f",
        "user-AbCd_0123456789",
    ],
    ids=["nanoid-64", "uuid-method", "custom-pattern"],
)
def test_every_short_uuid_remnawave_generates_fits_the_subscription_route(tmp_path: Path, token: str) -> None:
    """Remnawave draws short UUIDs from A-Z a-z 0-9 _ - at 16 to 64 characters: the route's own pattern."""
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.import_users(UserImportBatch(users=[_alice(sub_token=token)]))

        client.fetch_subscription(token)  # an unmatched route or unknown token raises not-found


@pytest.mark.parametrize("token", ["tooShort15chars", "has.dot.0123456789", "x" * 65], ids=["short", "dot", "long"])
def test_a_token_outside_the_route_pattern_is_refused(token: str) -> None:
    with pytest.raises(ValidationError):
        _alice(sub_token=token)


def test_a_version_five_database_gains_carried_traffic(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    ControlService(path, fleet_id=FLEET_ID, version="4.0.0").put_user("alice")
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE users DROP COLUMN traffic_carried_bytes")
    db.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
    db.commit()
    db.close()

    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.import_users(UserImportBatch(users=[_bob(used_traffic_bytes=GB)]))

    assert service.get_user("alice").lifetime_traffic_bytes == 0  # type: ignore[union-attr]
    assert service.get_user("bob").used_traffic_bytes == GB  # type: ignore[union-attr]


def test_the_client_reports_a_clash_as_a_conflict(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.import_users(UserImportBatch(users=[_alice()]))

        with pytest.raises(ControlServiceConflict):
            client.import_users(UserImportBatch(users=[_alice(uuid=BOB_UUID)]))
