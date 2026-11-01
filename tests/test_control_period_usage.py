"""Period directions agree across delivery and people reads, including resets and upgrades."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import UserImport, UserImportBatch, UserUpdate
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.core.agent_protocol import StatsReport, UserTraffic
from tests.support.control_service import FLEET_ID

NOW = "2026-11-01T12:00:00Z"
EPOCH = "6a1f0c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlService]:
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    yield service
    service.close()


def count(service: ControlService, up: int, down: int, seq: int, *, now: str = NOW, node: str = "main") -> None:
    user = service.get_user("sample")
    assert user is not None
    service.record_stats(
        node,
        StatsReport(
            counter_epoch=EPOCH,
            seq=seq,
            collected_at=now,
            users=[UserTraffic(user_id=user.user_id, uplink_bytes=up, downlink_bytes=down)],
        ),
        now=now,
    )


def assert_usage(service: ControlService, up: int, down: int, lifetime: int, *, unattributed: int = 0) -> None:
    user = service.get_user("sample")
    assert user is not None
    header = service.subscription(user.sub_token).headers["subscription-userinfo"]
    assert header.startswith(f"upload={up}; download={down};")
    info = json.loads(service.subscription(user.sub_token, "info").body)["user"]
    assert (info["trafficUploadBytes"], info["trafficDownloadBytes"], info["trafficUnattributedBytes"]) == (
        str(up),
        str(down),
        str(unattributed),
    )
    assert info["trafficUsedBytes"] == str(up + down)
    assert info["lifetimeTrafficUsedBytes"] == str(lifetime)
    assert user.used_traffic_bytes == up + down
    assert user.lifetime_traffic_bytes == lifetime
    assert user.to_data()["period_traffic"] == {
        "upload_bytes": up,
        "download_bytes": down,
        "unattributed_bytes": unattributed,
    }
    assert service.list_users()[0] == user


def test_header_reports_real_directions_and_manual_resets_leave_lifetime_untouched(service: ControlService) -> None:
    service.put_user("sample")
    count(service, 40, 90, 1)
    assert_usage(service, 40, 90, 130)
    service.reset_user_traffic("sample")
    assert_usage(service, 0, 0, 130)
    count(service, 47, 103, 2)
    count(service, 47, 103, 2)  # A replay never counts growth twice.
    count(service, 2, 8, 1, node="second")
    assert_usage(service, 9, 21, 160)


@pytest.mark.parametrize(
    ("strategy", "boundary"),
    [("day", "2026-11-02T00:00:00Z"), ("week", "2026-11-02T00:00:00Z"), ("month", "2026-12-01T00:00:00Z")],
)
def test_periodic_resets_advance_both_directions_before_counting_growth(
    service: ControlService, strategy: str, boundary: str
) -> None:
    service.put_user("sample")
    service.update_user("sample", UserUpdate.model_validate({"traffic_limit_strategy": strategy}))
    count(service, 40, 90, 1)
    count(service, 47, 103, 2, now=boundary)
    service.enforce(boundary)
    assert_usage(service, 7, 13, 150)


def test_imported_period_is_preserved_without_guessing_its_directions(service: ControlService) -> None:
    imported = UserImport(
        username="sample",
        uuid="00000000-0000-4000-8000-000000000001",
        sub_token="sample-token-0001",
        used_traffic_bytes=30,
        lifetime_traffic_bytes=100,
    )
    service.import_users(UserImportBatch(users=[imported]))
    count(service, 4, 9, 1)
    assert_usage(service, 4, 39, 113, unattributed=30)
    service.import_users(UserImportBatch(users=[imported.model_copy(update={"used_traffic_bytes": 20})], replace=True))
    assert_usage(service, 0, 20, 113, unattributed=20)
    count(service, 8, 15, 2)
    assert_usage(service, 4, 26, 123, unattributed=20)
    service.reset_user_traffic("sample")
    assert_usage(service, 0, 0, 123)


@pytest.mark.parametrize("reset", [False, True], ids=["native-lifetime", "combined-reset"])
def test_schema_seventeen_keeps_the_period_and_only_preserves_a_provable_split(
    service: ControlService, tmp_path: Path, reset: bool
) -> None:
    service.put_user("sample")
    count(service, 40, 90, 1)
    if reset:
        service.reset_user_traffic("sample")
        count(service, 47, 103, 2)
    lifetime = 150 if reset else 130
    service.close()
    database = tmp_path / "control.db"
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        for column in ("traffic_upload_offset_bytes", "traffic_download_offset_bytes", "traffic_period_carried_bytes"):
            if column in columns:
                db.execute(f"ALTER TABLE users DROP COLUMN {column}")
        db.execute("UPDATE meta SET value = '17' WHERE key = 'schema_version'")
    for _ in range(2):
        migrated = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
        try:
            assert migrated._store.meta("schema_version") == str(SCHEMA_VERSION)
            assert_usage(migrated, 0 if reset else 40, 20 if reset else 90, lifetime, unattributed=20 if reset else 0)
        finally:
            migrated.close()
    migrated = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    try:
        count(migrated, (47 if reset else 40) + 4, (103 if reset else 90) + 6, 3)
        assert_usage(migrated, 4 if reset else 44, 26 if reset else 96, lifetime + 10, unattributed=20 if reset else 0)
        migrated.reset_user_traffic("sample")
        assert_usage(migrated, 0, 0, lifetime + 10)
    finally:
        migrated.close()
