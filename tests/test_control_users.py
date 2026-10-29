"""Control-service users: operator changes, reissue, expiry enforcement, and schema migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meridian.control.models import NodeSpec, UserUpdate, users_resource_id
from meridian.control.service import ControlService, ServiceError

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
