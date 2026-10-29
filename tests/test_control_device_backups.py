"""Device privacy through snapshots, restored HMAC keys, and schema steps nine to eleven."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meridian.control.models import DeviceKeyCreate, PrivacyChange, PrivacySettings
from meridian.control.restore import stage_restored_database
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from tests.support.control_service import FLEET_ID, write_privacy


@pytest.mark.parametrize(
    ("level", "snapshotted", "restored"),
    [
        ("off", 0, 0),
        ("private", 1, 0),
        ("full", 1, 1),
    ],
    ids=["off-drops-stale-rows", "private-registers-again", "full-keeps-devices"],
)
def test_snapshots_carry_privacy_and_on_devices_but_private_restore_needs_new_registrations(
    tmp_path: Path,
    level: str,
    snapshotted: int,
    restored: int,
) -> None:
    database = tmp_path / "live.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    key = generate_key_pair()
    try:
        user = service.put_user("alice")
        initial = PrivacySettings(device_limits="private" if level == "private" else "full")
        write_privacy(database, initial)
        service.subscription(
            user.sub_token, device_headers={"x-hwid": "device-00000001", "x-device-model": "Example model"}
        )
        settings = PrivacySettings(device_limits=level, default_device_limit=7)  # type: ignore[arg-type]
        write_privacy(database, settings)
        change = PrivacyChange(
            changed_at="2026-10-29T12:00:00Z", author="owner", before=PrivacySettings(), after=settings
        )
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO privacy_changes (changed_at, author, before, after) VALUES (?, ?, ?, ?)",
                (change.changed_at, change.author, change.before.model_dump_json(), change.after.model_dump_json()),
            )
        service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=key.public))
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
    finally:
        service.close()
    copy = sqlite3.connect(":memory:")
    try:
        copy.deserialize(snapshot.database)
        assert copy.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == snapshotted
        assert copy.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
        assert (
            PrivacySettings.model_validate_json(copy.execute("SELECT settings FROM privacy").fetchone()[0]) == settings
        )
        assert copy.execute("SELECT author FROM privacy_changes").fetchone()[0] == "owner"
    finally:
        copy.close()
    restored_path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0")
    service = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.privacy() == settings and service.user_devices("alice").count == restored
        response = service.subscription(user.sub_token, device_headers={"x-hwid": "device-00000001"})
        assert "x-hwid-limit" not in response.headers
        assert service.user_devices("alice").count == (0 if level == "off" else 1)
    finally:
        service.close()


def test_a_full_restore_also_forgets_private_fingerprints_still_waiting_for_a_refresh(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    key = generate_key_pair()
    try:
        user = service.put_user("alice")
        write_privacy(path, PrivacySettings(device_limits="private", default_device_limit=1))
        service.subscription(user.sub_token, device_headers={"x-hwid": "device-00000001"})
        write_privacy(path, PrivacySettings(device_limits="full", default_device_limit=1))
        service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=key.public))
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
    finally:
        service.close()
    restored_path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.user_devices("alice").count == 0
        assert (
            "x-hwid-limit"
            not in restored.subscription(user.sub_token, device_headers={"x-hwid": "device-00000001"}).headers
        )
    finally:
        restored.close()


@pytest.mark.parametrize("old_version", [9, 10], ids=["before-privacy", "with-privacy-and-history"])
def test_schema_eleven_keeps_users_privacy_and_history_tables(tmp_path: Path, old_version: int) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("alice")
    service.close()
    settings = PrivacySettings(device_limits="private", default_device_limit=7)
    write_privacy(path, settings)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE devices")
        db.execute("ALTER TABLE users DROP COLUMN device_limit")
        if old_version == 9:
            for table in ("privacy", "privacy_changes", "traffic_daily", "node_traffic_daily"):
                db.execute(f"DROP TABLE {table}")
        else:
            db.execute("INSERT INTO traffic_daily VALUES (?, '2026-10-29', 10, 20)", (user.user_id,))
        db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(old_version),))
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.put_user("alice") == user and service.put_user("alice").device_limit is None
        assert service.privacy() == (PrivacySettings() if old_version == 9 else settings)
        assert service.user_devices("alice").count == 0
    finally:
        service.close()
    with sqlite3.connect(path) as db:
        assert (
            int(db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]) == SCHEMA_VERSION == 11
        )
        if old_version == 10:
            assert db.execute("SELECT up, down FROM traffic_daily").fetchone() == (10, 20)
