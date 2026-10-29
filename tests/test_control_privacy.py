"""Privacy defaults, atomic downgrades, auditing, schema 10, and snapshot/restore policy."""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.control.backups import strip_unsnapshotted
from meridian.control.client import ControlClient, ControlServiceScopeError
from meridian.control.devices import DEVICE_HMAC_SECRET
from meridian.control.models import DeviceKeyCreate, PrivacySettings, ServiceSettings
from meridian.control.restore import stage_restored_database
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, drop_token_expiry_schema, serve_control

NOW = "2026-10-29T12:00:00Z"
DEVICE_TABLE = """CREATE TABLE IF NOT EXISTS devices (
    user_id TEXT NOT NULL, device_key TEXT NOT NULL,
    platform TEXT, os_version TEXT, model TEXT, user_agent TEXT,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    PRIMARY KEY (user_id, device_key)
)"""


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ControlService]:
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    yield service
    service.close()


def _device(service: ControlService, key: str = "device-id") -> None:
    service._store.create(DEVICE_TABLE)
    user = service.put_user("alice")
    service._db.execute(
        "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user.user_id, key, "system", "version", "model", "app", NOW, NOW),
    )


def _history(service: ControlService, day: str = "2026-10-29") -> None:
    user = service.put_user("alice")
    service._db.execute("INSERT INTO traffic_daily VALUES (?, ?, 10, 20)", (user.user_id, day))
    service._db.execute("INSERT INTO node_traffic_daily VALUES ('main', ?, 30)", (day,))


def test_absent_privacy_is_off_and_putting_the_defaults_back_records_no_change(service: ControlService) -> None:
    assert service.privacy() == PrivacySettings()
    assert service.privacy_state().changes == []
    result = service.set_privacy(PrivacySettings(), author="root")
    assert result.changed is False and result.changes == []
    assert service._store.row("SELECT * FROM privacy") is None
    assert service._store.row("SELECT 1 FROM devices") is None
    assert service._store.secret(DEVICE_HMAC_SECRET) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_limits", "raw"),
        ("usage_history", "hourly"),
        ("default_device_limit", 0),
        ("default_device_limit", 101),
        ("history_days", 6),
        ("history_days", 731),
    ],
)
def test_privacy_bounds_refuse_an_unbounded_or_unknown_opt_in(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PrivacySettings.model_validate({field: value})


def test_settings_apply_never_replaces_privacy_and_a_repeat_never_adds_an_audit_row(service: ControlService) -> None:
    settings = PrivacySettings(device_limits="private", usage_history="daily", history_days=30)
    first = service.set_privacy(settings, author="laptop")
    service.put_settings(ServiceSettings(profile_title="Ours"))
    service.put_user("alice")
    again = service.set_privacy(settings, author="other token")
    assert service.privacy() == settings
    assert first.changed and not again.changed and again.changes == first.changes
    assert first.changes[0].model_dump() == {
        "changed_at": NOW,
        "author": "laptop",
        "before": PrivacySettings().model_dump(),
        "after": settings.model_dump(),
    }
    assert service._store.secret(DEVICE_HMAC_SECRET) is None  # no HWID has been read yet


def test_change_order_is_insertion_order_even_when_the_clock_ties_and_only_fifty_are_returned(
    service: ControlService,
) -> None:
    for i in range(60):
        service.set_privacy(PrivacySettings(history_days=100 + i), author=f"token-{i}")
    changes = service.privacy_state().changes
    assert len(changes) == 50
    assert [change.author for change in changes] == [f"token-{i}" for i in reversed(range(10, 60))]
    assert service._store.row("SELECT COUNT(*) AS n FROM privacy_changes")["n"] == 60


@pytest.mark.parametrize(
    ("before", "after", "deleted", "rekeyed"),
    [
        ("full", "private", 0, 1),
        ("full", "off", 1, 0),
        ("private", "off", 1, 0),
        ("private", "full", 0, 0),
    ],
    ids=["full-to-private-rekeys", "full-to-off-deletes", "private-to-off-deletes", "private-to-full-keeps-slots"],
)
def test_lowering_devices_removes_metadata_and_raising_to_full_keeps_every_registration(
    service: ControlService,
    before: str,
    after: str,
    deleted: int,
    rekeyed: int,
) -> None:
    service.set_privacy(PrivacySettings.model_validate({"device_limits": before}), author="root")
    _device(service)
    result = service.set_privacy(PrivacySettings.model_validate({"device_limits": after}), author="owner")
    assert (result.deleted.devices, result.rekeyed_devices) == (deleted, rekeyed)
    rows = service._store.rows("SELECT * FROM devices")
    if rekeyed:
        secret = service._store.secret(DEVICE_HMAC_SECRET)
        assert secret is not None
        assert rows[0]["device_key"] == hmac.digest(secret, b"device-id", "sha256").hex()
        assert tuple(rows[0][name] for name in ("platform", "os_version", "model", "user_agent")) == (None,) * 4
    elif after == "full":
        # A fingerprint keeps its slot until the device's next fetch tells full mode its raw ID.
        assert [row["device_key"] for row in rows] == ["device-id"]
    else:
        assert rows == []
    assert service.set_privacy(result.settings, author="root").changed is False


def test_rekeying_does_not_collide_with_a_raw_id_equal_to_another_rows_future_hash(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(device_limits="full"), author="root")
    key = b"k" * 32
    service._store.set_secret(DEVICE_HMAC_SECRET, key)
    future_hash = hmac.digest(key, b"device-id", "sha256").hex()
    _device(service, future_hash)
    _device(service)
    result = service.set_privacy(PrivacySettings(device_limits="private"), author="root")
    assert result.rekeyed_devices == 2
    assert {row[0] for row in service._db.execute("SELECT device_key FROM devices")} == {
        future_hash,
        hmac.digest(key, future_hash.encode(), "sha256").hex(),
    }


def test_daily_to_off_deletes_both_histories_and_preserves_lifetime_traffic(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    _history(service)
    user = service.get_user("alice")
    assert user is not None
    service._db.execute(
        "INSERT INTO traffic_totals (user_id, uplink_bytes, downlink_bytes, last_seen_at) VALUES (?, 10, 20, ?)",
        (user.user_id, NOW),
    )
    result = service.set_privacy(PrivacySettings(), author="owner")
    assert result.deleted.model_dump() == {"devices": 0, "traffic_daily": 1, "node_traffic_daily": 1}
    assert service._store.rows("SELECT * FROM traffic_daily") == []
    assert service._store.rows("SELECT * FROM node_traffic_daily") == []
    assert service.get_user("alice").lifetime_traffic_bytes == 30


def test_an_audit_failure_rolls_back_the_new_key_downgrade_deletions_and_settings(service: ControlService) -> None:
    initial = PrivacySettings(device_limits="full", usage_history="daily")
    service.set_privacy(initial, author="root")
    _device(service)
    _history(service)
    service._store.create(
        "CREATE TRIGGER no_audit BEFORE INSERT ON privacy_changes BEGIN SELECT RAISE(ABORT, 'audit failed'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError, match="audit failed"):
        service.set_privacy(PrivacySettings(device_limits="private"), author="owner")
    assert service.privacy() == initial
    assert service._store.row("SELECT device_key FROM devices")["device_key"] == "device-id"
    assert service._store.row("SELECT bytes FROM node_traffic_daily")["bytes"] == 30
    assert service._store.row("SELECT up FROM traffic_daily")["up"] == 10
    assert len(service.privacy_state().changes) == 1
    assert service._store.secret(DEVICE_HMAC_SECRET) is None


def test_lowering_retention_prunes_in_the_settings_transaction(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    _history(service, "2026-10-22")
    _history(service, "2026-10-23")
    result = service.set_privacy(PrivacySettings(usage_history="daily", history_days=7), author="owner")
    assert result.deleted.traffic_daily == result.deleted.node_traffic_daily == 1
    assert service._db.execute("SELECT day FROM traffic_daily").fetchall() == [("2026-10-23",)]


def test_schema_nine_migrates_once_through_steps_ten_and_eleven_without_changing_totals(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("alice")
    service._db.execute(
        "INSERT INTO traffic_totals (user_id, uplink_bytes, downlink_bytes, last_seen_at) VALUES (?, 3, 7, ?)",
        (user.user_id, NOW),
    )
    service.close()
    with sqlite3.connect(path) as db:
        from tests.support.control_service import drop_operations_schema

        drop_operations_schema(db)
        for table in ("privacy", "privacy_changes", "traffic_daily", "node_traffic_daily", "devices"):
            db.execute(f"DROP TABLE {table}")
        db.execute("ALTER TABLE users DROP COLUMN device_limit")
        drop_token_expiry_schema(db)
        db.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    for _ in range(2):
        migrated = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
        assert migrated._store.meta("schema_version") == str(SCHEMA_VERSION)
        assert migrated.privacy() == PrivacySettings()
        assert migrated.get_user("alice").lifetime_traffic_bytes == 10
        migrated.close()


def test_privacy_reads_need_read_and_writes_need_admin_and_audit_the_token_name(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        assert admin.privacy().settings == PrivacySettings()
        token = admin.create_token("laptop", "admin")
        with ControlClient(url, token.token) as writer:
            changed = writer.set_privacy(PrivacySettings(usage_history="daily"))
        assert changed.changes[0].author == "laptop"
        for scope in ("read", "propose", "approve"):
            token = admin.create_token(scope, scope)
            with ControlClient(url, token.token) as reader:
                assert reader.privacy().settings.usage_history == "daily"
                with pytest.raises(ControlServiceScopeError):
                    reader.set_privacy(PrivacySettings())
        assert admin.set_privacy(PrivacySettings()).changes[0].author == "root"


@pytest.mark.parametrize("devices", ["off", "private", "full"])
@pytest.mark.parametrize("history", ["off", "daily"])
def test_snapshots_carry_settings_and_audit_but_rows_only_when_their_level_is_on(
    service: ControlService,
    devices: str,
    history: str,
) -> None:
    settings = PrivacySettings.model_validate({"device_limits": devices, "usage_history": history})
    service.set_privacy(settings, author="owner")
    _device(service)
    _history(service)
    backup = generate_key_pair()
    service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=backup.public))
    snapshot = decrypt_snapshot(service.snapshot(), [backup.secret])
    with sqlite3.connect(":memory:") as db:
        db.deserialize(snapshot.database)
        assert db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == (devices != "off")
        assert db.execute("SELECT COUNT(*) FROM traffic_daily").fetchone()[0] == (history != "off")
        assert db.execute("SELECT COUNT(*) FROM node_traffic_daily").fetchone()[0] == (history != "off")
        row = db.execute("SELECT settings FROM privacy").fetchone()
        assert (PrivacySettings.model_validate_json(row[0]) if row else PrivacySettings()) == settings
        assert db.execute("SELECT COUNT(*) FROM privacy_changes").fetchone()[0] == len(service.privacy_state().changes)


@pytest.mark.parametrize(("devices", "kept"), [("private", 0), ("full", 1)])
def test_a_restore_keeps_privacy_and_history_but_private_devices_reregister_without_the_old_fleet_key(
    service: ControlService,
    tmp_path: Path,
    devices: str,
    kept: int,
) -> None:
    service.set_privacy(
        PrivacySettings.model_validate({"device_limits": devices, "usage_history": "daily"}), author="owner"
    )
    _device(service)
    _history(service)
    service._store.set_secret(DEVICE_HMAC_SECRET, b"old fleet key")
    backup = generate_key_pair()
    service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=backup.public))
    database = decrypt_snapshot(service.snapshot(), [backup.secret]).database
    stage_restored_database(database, tmp_path / "restored.db", fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(tmp_path / "restored.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.privacy_state() == service.privacy_state()
        assert restored._store.secret(DEVICE_HMAC_SECRET) is None
        assert restored._db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == kept
        assert restored._db.execute("SELECT COUNT(*) FROM traffic_daily").fetchone()[0] == 1
    finally:
        restored.close()


def test_stripping_an_old_schema_with_no_privacy_or_device_tables_still_works() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE secrets (name TEXT, value BLOB)")
        db.execute("INSERT INTO secrets VALUES ('old', X'01')")
        strip_unsnapshotted(db, restoring=True)
        assert db.execute("SELECT * FROM secrets").fetchall() == []
