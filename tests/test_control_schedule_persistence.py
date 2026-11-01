"""Account schedules survive encrypted restore and the single forward schema step."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import DeviceKeyCreate, UserCreate
from meridian.control.restore import stage_restored_database
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.core.account_schedule import AccountLifecycleState
from meridian.core.agent_protocol import StatsReport, UserTraffic
from meridian.core.client_limits import UserCreationSettings
from meridian.core.topology import SetupIntent
from meridian.servers import ServerRegistry
from meridian.setup.fleet_document import (
    dump_fleet_document,
    fleet_document,
    intent_from_document,
    parse_fleet_document,
)
from tests.support.control_service import FLEET_ID, drop_account_schedule_schema, drop_period_usage_schema
from tests.support.topology import single_exit_intent


@pytest.mark.parametrize("activated", [False, True])
def test_snapshot_and_restore_keep_pending_duration_or_renewal_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, activated: bool
) -> None:
    clock = ["2027-01-31T12:00:00Z"]
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: clock[0])

    def current() -> float:
        return datetime.fromisoformat(clock[0]).timestamp()

    service = ControlService(tmp_path / "live.db", fleet_id=FLEET_ID, version="4.0.0", clock=current)
    key = generate_key_pair()
    try:
        person = service.create_user(
            UserCreate(
                username="example",
                activation_duration_days=30,
                renewal_interval_months=1,
                renewal_limit=5,
                traffic_limit_strategy="month_rolling",
                traffic_reset_anchor_day=31,
                device_limit=0,
            )
        )
        if activated:
            service.record_stats(
                "main",
                StatsReport(
                    counter_epoch="6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b",
                    seq=1,
                    collected_at=clock[0],
                    users=[UserTraffic(user_id=person.user_id, uplink_bytes=1, downlink_bytes=9)],
                ),
            )
            clock[0] = "2027-05-04T12:00:00Z"
            service.enforce()
        before = service.get_user("example")
        service.backups.register_device(DeviceKeyCreate(name="example-device", public_key=key.public))
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
    finally:
        service.close()
    restored_path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0", clock=current)
    restored = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0", clock=current)
    try:
        assert restored.get_user("example") == before
        assert restored.get_user("example").schedule.activation_pending is not activated
        assert restored.get_user("example").device_limit == 0
    finally:
        restored.close()


@pytest.mark.parametrize("old_version", [17, 18], ids=["combined-period", "directional-period"])
def test_schedule_upgrade_keeps_creation_day_period_usage_and_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_version: int
) -> None:
    now = "2027-01-31T12:00:00Z"
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: now)

    def clock() -> float:
        return datetime.fromisoformat(now).timestamp()

    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    person = service.put_user("example")
    for seq, up, down in ((1, 40, 90), (2, 47, 103)):
        service.record_stats(
            "main",
            StatsReport(
                counter_epoch="6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b",
                seq=seq,
                collected_at=now,
                users=[UserTraffic(user_id=person.user_id, uplink_bytes=up, downlink_bytes=down)],
            ),
        )
        if seq == 1:
            service.reset_user_traffic("example")
    service.close()
    with sqlite3.connect(path) as db:
        if old_version == 17:
            drop_period_usage_schema(db)
        else:
            drop_account_schedule_schema(db)
        db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(old_version),))
    for _ in range(2):
        service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=clock)
        try:
            person = service.get_user("example")
            assert person.traffic_reset_anchor_day == 31 and person.activation_duration_days is None
            assert person.used_traffic_bytes == 20 and person.lifetime_traffic_bytes == 150
            assert person.period_traffic.to_data() == {
                "upload_bytes": 0 if old_version == 17 else 7,
                "download_bytes": 20 if old_version == 17 else 13,
                "unattributed_bytes": 20 if old_version == 17 else 0,
            }
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(
                    SCHEMA_VERSION
                )
        finally:
            service.close()


def test_fleet_document_keeps_creation_schedules_and_default_payloads_stay_small(tmp_path: Path) -> None:
    settings = UserCreationSettings(
        activation_duration_days=30,
        renewal_interval_months=1,
        renewal_limit=2,
        traffic_limit_strategy="month_rolling",
        traffic_reset_anchor_day=31,
        device_limit=0,
    )
    data = single_exit_intent(users=["example"]).model_dump(mode="json")
    data["access"]["creation_settings"] = {"example": settings.to_data()}
    intent = SetupIntent.model_validate(data)
    registry = ServerRegistry(tmp_path / "servers.json")
    document = fleet_document(intent, registry)
    restored = intent_from_document(parse_fleet_document(dump_fleet_document(document)), intent, registry)
    assert restored.access.creation_settings["example"] == settings
    assert not any(field in UserCreationSettings().to_data() for field in AccountLifecycleState.model_fields)
