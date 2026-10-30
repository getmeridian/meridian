"""Control-service users: operator changes, reissue, expiry enforcement, and schema migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meridian.control.models import NodeSpec, UserUpdate, users_resource_id
from meridian.control.service import ControlService, ServiceError
from meridian.core.agent_protocol import StatsReport, UserTraffic

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
TAG = "meridian-main-reality"
PAST = "2026-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    return ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")


def _with_node(service: ControlService) -> None:
    config = {"inbounds": [{"tag": TAG, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}}]}
    spec = NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "core-config",
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "config": config,
                    "api_address": "127.0.0.1:10085",
                }
            ],
            "managed_inbounds": [{"tag": TAG, "protocol": "vless", "flow": "xtls-rprx-vision"}],
        }
    )
    service.put_node_spec("main", spec)


def _delivered(service: ControlService) -> list[str]:
    """User IDs in the node document's users resource."""
    document = service.poll("main", _poll())["desired"]
    users = next(item for item in document["resources"] if item["id"] == users_resource_id(TAG))
    return [user["user_id"] for user in users["users"]]


def _poll():
    from meridian.core.agent_protocol import PollRequest

    return PollRequest(have=None, wait_seconds=0)


def test_disabling_a_user_takes_them_out_of_node_documents(service: ControlService) -> None:
    _with_node(service)
    alice = service.put_user("alice")
    bob = service.put_user("bob")

    service.update_user("alice", UserUpdate(status="disabled"))

    assert _delivered(service) == [bob.user_id]
    service.update_user("alice", UserUpdate(status="active"))
    assert sorted(_delivered(service)) == sorted([alice.user_id, bob.user_id])


def test_update_keeps_fields_it_does_not_name(service: ControlService) -> None:
    service.put_user("alice")
    service.update_user(
        "alice", UserUpdate(traffic_limit_bytes=10**9, traffic_limit_strategy="month", expire_at=FUTURE)
    )

    updated = service.update_user("alice", UserUpdate(status="disabled"))

    assert (updated.traffic_limit_bytes, updated.traffic_limit_strategy, updated.expire_at) == (10**9, "month", FUTURE)
    cleared = service.update_user("alice", UserUpdate.model_validate({"expire_at": None}))
    assert cleared.expire_at is None


def test_reissue_replaces_link_credentials_only(service: ControlService) -> None:
    before = service.put_user("alice")

    after = service.reissue_user("alice")

    assert (after.user_id, after.username) == (before.user_id, before.username)
    assert after.uuid != before.uuid and after.sub_token != before.sub_token


def test_enforcement_expires_active_users_once_their_time_passes(service: ControlService) -> None:
    _with_node(service)
    service.put_user("alice")
    service.update_user("alice", UserUpdate(expire_at="2999-06-01T00:00:00Z"))

    assert service.enforce("2999-05-31T23:59:59.5Z") == []
    assert service.enforce("2999-06-01T00:00:00Z") == ["alice"]
    assert service.get_user("alice").status == "expired"  # type: ignore[union-attr]
    assert _delivered(service) == []


def test_setting_a_past_expiry_expires_at_once(service: ControlService) -> None:
    service.put_user("alice")

    assert service.update_user("alice", UserUpdate(expire_at=PAST)).status == "expired"


def test_extending_an_expired_user_makes_them_active_again(service: ControlService) -> None:
    service.put_user("alice")
    service.update_user("alice", UserUpdate(expire_at=PAST))

    assert service.update_user("alice", UserUpdate(expire_at=FUTURE)).status == "active"


def test_a_disabled_user_stays_disabled_past_expiry(service: ControlService) -> None:
    service.put_user("alice")
    service.update_user("alice", UserUpdate(status="disabled", expire_at=PAST))

    assert service.enforce() == []
    assert service.get_user("alice").status == "disabled"  # type: ignore[union-attr]


def test_unknown_user_changes_are_not_found(service: ControlService) -> None:
    with pytest.raises(ServiceError) as error:
        service.update_user("nobody", UserUpdate(status="disabled"))
    assert error.value.status == 404


def test_a_version_one_database_gains_user_limits(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE users (username TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, uuid TEXT NOT NULL UNIQUE,
            sub_token TEXT NOT NULL UNIQUE, status TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    db.execute("INSERT INTO meta VALUES ('fleet_id', ?), ('schema_version', '1')", (FLEET_ID,))
    db.execute(
        "INSERT INTO users VALUES ('alice', 'u1', '00000000-0000-4000-8000-000000000001', "
        "'token-for-alice-0000000', 'active', '2026-10-01T00:00:00Z')"
    )
    db.commit()
    db.close()

    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")

    alice = service.get_user("alice")
    assert alice is not None and (alice.traffic_limit_bytes, alice.expire_at) == (0, None)
    assert service.update_user("alice", UserUpdate(traffic_limit_bytes=5)).traffic_limit_bytes == 5


def test_a_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()

    with pytest.raises(ServiceError, match="newer"):
        ControlService(path, fleet_id=FLEET_ID, version="4.0.0")


def test_subscription_userinfo_carries_the_users_limit_and_expiry(service: ControlService) -> None:
    user = service.put_user("alice")
    service.update_user("alice", UserUpdate(traffic_limit_bytes=5 * 10**9, expire_at="2030-01-01T00:00:00Z"))

    info = service.subscription(user.sub_token).headers["subscription-userinfo"]

    assert "total=5000000000" in info
    assert "expire=1893456000" in info


EPOCH = "6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b"


def _count(service: ControlService, user_id: str, total: int, seq: int) -> None:
    """One agent report of *total* cumulative bytes (half up, half down) for one user."""
    report = StatsReport(
        counter_epoch=EPOCH,
        seq=seq,
        collected_at="2026-10-29T12:00:00Z",
        users=[UserTraffic(user_id=user_id, uplink_bytes=total // 2, downlink_bytes=total - total // 2)],
    )
    service.traffic.record("main", report)


def test_a_user_reaching_their_limit_is_limited_and_leaves_node_documents(service: ControlService) -> None:
    _with_node(service)
    alice = service.put_user("alice")
    service.update_user("alice", UserUpdate(traffic_limit_bytes=1000))

    _count(service, alice.user_id, 999, seq=1)
    assert service.enforce() == []
    _count(service, alice.user_id, 1000, seq=2)

    assert service.enforce() == ["alice"]
    assert service.get_user("alice").status == "limited"  # type: ignore[union-attr]
    assert _delivered(service) == []


def test_raising_the_limit_or_resetting_traffic_makes_a_limited_user_active(service: ControlService) -> None:
    alice = service.put_user("alice")
    service.update_user("alice", UserUpdate(traffic_limit_bytes=1000))
    _count(service, alice.user_id, 1500, seq=1)
    service.enforce()

    assert service.update_user("alice", UserUpdate(traffic_limit_bytes=2000)).status == "active"
    service.update_user("alice", UserUpdate(traffic_limit_bytes=1000))
    reset = service.reset_user_traffic("alice")

    assert (reset.status, reset.used_traffic_bytes, reset.lifetime_traffic_bytes) == ("active", 0, 1500)


@pytest.mark.parametrize(
    ("strategy", "inside", "next_period"),
    [
        ("day", "2026-10-29T23:59:59Z", "2026-10-30T00:00:00Z"),
        ("week", "2026-11-01T23:59:59Z", "2026-11-02T00:00:00Z"),
        ("month", "2026-11-30T23:59:59Z", "2026-12-01T00:00:00Z"),
    ],
    ids=["day", "week-starts-monday", "month"],
)
def test_a_new_traffic_period_resets_use(
    service: ControlService, monkeypatch: pytest.MonkeyPatch, strategy: str, inside: str, next_period: str
) -> None:
    # Edits enforce at the service's own clock; a real one inside `next_period`'s period hid the reset.
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: inside.replace("23:59:59", "00:00:01"))
    alice = service.put_user("alice")
    service.update_user("alice", UserUpdate(traffic_limit_bytes=1000, traffic_limit_strategy=strategy))  # type: ignore[arg-type]
    service.enforce(inside.replace("23:59:59", "00:00:01"))
    _count(service, alice.user_id, 1200, seq=1)

    assert service.enforce(inside) == ["alice"]
    assert service.enforce(next_period) == ["alice"]
    assert service.get_user("alice").used_traffic_bytes == 0  # type: ignore[union-attr]


def test_traffic_arriving_after_midnight_counts_toward_the_new_day_and_limits_it(
    service: ControlService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: "2026-10-29T00:00:01Z")
    alice = service.put_user("alice")
    service.update_user("alice", UserUpdate(traffic_limit_bytes=1000, traffic_limit_strategy="day"))
    service.enforce("2026-10-29T00:00:01Z")

    def stats(total: int, seq: int, now: str) -> None:
        traffic = UserTraffic(user_id=alice.user_id, uplink_bytes=total, downlink_bytes=0)
        report = StatsReport(counter_epoch=EPOCH, seq=seq, collected_at=now, users=[traffic])
        service.record_stats("main", report, now)

    stats(500, 1, "2026-10-29T23:59:00Z")
    stats(1700, 2, "2026-10-30T00:00:05Z")
    service.enforce("2026-10-30T00:00:20Z")  # the timer's next tick must not fold the new day's use away

    alice_now = service.get_user("alice")
    assert alice_now is not None and (alice_now.used_traffic_bytes, alice_now.status) == (1200, "limited")


def test_traffic_without_a_reset_strategy_accumulates(service: ControlService) -> None:
    alice = service.put_user("alice")
    _count(service, alice.user_id, 1200, seq=1)
    service.enforce("2999-01-01T00:00:00Z")

    assert service.get_user("alice").used_traffic_bytes == 1200  # type: ignore[union-attr]


def test_subscription_userinfo_reports_the_users_traffic(service: ControlService) -> None:
    alice = service.put_user("alice")
    _count(service, alice.user_id, 3001, seq=1)

    info = service.subscription(alice.sub_token).headers["subscription-userinfo"]

    # Like the panel, the header carries all use as download.
    assert "upload=0; download=3001" in info
