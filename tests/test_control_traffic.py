"""Control service: cumulative per-user traffic from agents' stats reports, counted exactly once."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meridian.control.errors import ServiceError
from meridian.control.service import ControlService
from meridian.control.traffic import KEPT_EPOCHS, REMEMBERED_RETIRED_EPOCHS
from meridian.core.agent_protocol import StatsReport
from tests.support.agent_node import FLEET_ID
from tests.support.control_service import drop_device_privacy_schema

EPOCH_A = "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
EPOCH_B = "6c1d8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    service.put_user("bob")
    return service


def _report(service: ControlService, epoch: str, seq: int, **totals: tuple[int, int]) -> StatsReport:
    users = []
    for name, (up, down) in totals.items():
        user = service.get_user(name)
        assert user is not None
        users.append({"user_id": user.user_id, "uplink_bytes": up, "downlink_bytes": down})
    return StatsReport.model_validate(
        {"type": "stats", "counter_epoch": epoch, "seq": seq, "collected_at": "2026-10-29T12:00:00Z", "users": users}
    )


def _totals(service: ControlService) -> dict[str, tuple[int, int]]:
    return {item.username: (item.uplink_bytes, item.downlink_bytes) for item in service.traffic.summary().users}


def test_growth_is_counted_once_and_resent_reports_only_ack(service: ControlService) -> None:
    first = _report(service, EPOCH_A, 1, alice=(100, 1000))
    assert service.traffic.record("main", first).acked_seq == 1
    assert service.traffic.record("main", first).acked_seq == 1
    service.traffic.record("main", _report(service, EPOCH_A, 2, alice=(150, 1500), bob=(5, 7)))
    stale = service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(999, 999)))

    assert stale.acked_seq == 2
    assert _totals(service) == {"alice": (150, 1500), "bob": (5, 7)}


def test_a_user_missing_from_a_report_keeps_its_total(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(10, 20), bob=(1, 2)))
    service.traffic.record("main", _report(service, EPOCH_A, 2, alice=(30, 40)))
    assert _totals(service) == {"alice": (30, 40), "bob": (1, 2)}


def test_a_new_counter_epoch_starts_from_zero_and_a_late_report_of_the_old_one_counts_only_its_growth(
    service: ControlService,
) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 5, alice=(100, 100)))
    service.traffic.record("main", _report(service, EPOCH_B, 1, alice=(10, 20)))
    late = service.traffic.record("main", _report(service, EPOCH_A, 6, alice=(500, 500)))
    service.traffic.record("main", _report(service, EPOCH_B, 2, alice=(30, 40)))

    assert (late.counter_epoch, late.acked_seq) == (EPOCH_A, 6)
    assert _totals(service)["alice"] == (530, 540)


def test_a_delayed_report_from_an_unseen_epoch_never_retires_the_live_one(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_B, 1, alice=(10, 0)))
    service.traffic.record("main", _report(service, EPOCH_A, 9, alice=(100, 0)))
    service.traffic.record("main", _report(service, EPOCH_B, 2, alice=(1010, 0)))
    service.traffic.record("main", _report(service, EPOCH_B, 3, alice=(2010, 0)))

    assert _totals(service)["alice"] == (2110, 0)
    assert service.traffic.node_totals() == {"main": 2010}


def test_only_the_most_recent_epochs_keep_baselines_and_older_ones_never_count_again(
    service: ControlService,
) -> None:
    epochs = [f"{index}c1d8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00" for index in range(KEPT_EPOCHS + 1)]
    for epoch in epochs:
        service.traffic.record("main", _report(service, epoch, 1, alice=(1, 0)))

    replay = service.traffic.record("main", _report(service, epochs[0], 2, alice=(500, 0)))

    assert replay.acked_seq == 2
    assert _totals(service)["alice"] == (KEPT_EPOCHS + 1, 0)


def test_counters_of_users_the_service_never_had_are_dropped(service: ControlService) -> None:
    """A node could otherwise fill control.db with made-up user IDs; known users still count."""
    report = _report(service, EPOCH_A, 1, alice=(10, 20))
    forged = report.model_copy(
        update={"users": [*report.users, *(user.model_copy(update={"user_id": "u0ghost"}) for user in report.users)]}
    )

    service.record_stats("main", forged)

    assert _totals(service)["alice"] == (10, 20)
    rows = service.traffic._store.rows("SELECT user_id FROM traffic_counters", ())
    assert [row["user_id"] for row in rows] == [report.users[0].user_id]


def test_a_node_that_keeps_rotating_epochs_is_remembered_only_so_far(service: ControlService) -> None:
    for index in range(KEPT_EPOCHS + REMEMBERED_RETIRED_EPOCHS + 5):
        service.traffic.record("main", _report(service, f"{index:08x}-6c1d-4e8b-9a7f-2b5d4c3e1f00", 1, alice=(1, 0)))

    rows = service.traffic._store.rows("SELECT COUNT(*) AS n FROM traffic_retired_epochs", ())
    assert rows[0]["n"] == REMEMBERED_RETIRED_EPOCHS


def test_a_version_eight_database_keeps_each_nodes_live_baselines(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    alice = service.put_user("alice").user_id
    db = sqlite3.connect(path)
    drop_device_privacy_schema(db)
    db.executescript(
        "DROP TABLE traffic_reports; DROP TABLE traffic_counters;"
        "CREATE TABLE traffic_reports (node_id TEXT PRIMARY KEY, counter_epoch TEXT NOT NULL, seq INTEGER NOT NULL);"
        "CREATE TABLE traffic_counters (node_id TEXT NOT NULL, user_id TEXT NOT NULL, uplink_bytes INTEGER NOT NULL,"
        " downlink_bytes INTEGER NOT NULL, PRIMARY KEY (node_id, user_id));"
        f"INSERT INTO traffic_reports VALUES ('main', '{EPOCH_A}', 4);"
        f"INSERT INTO traffic_counters VALUES ('main', '{alice}', 100, 200);"
        f"INSERT INTO traffic_totals VALUES ('{alice}', 100, 200, NULL);"
        "UPDATE meta SET value = '8' WHERE key = 'schema_version';"
    )
    db.close()

    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    assert service.traffic.record("main", _report(service, EPOCH_A, 4, alice=(100, 200))).acked_seq == 4
    service.traffic.record("main", _report(service, EPOCH_A, 5, alice=(150, 260)))

    assert _totals(service)["alice"] == (150, 260)


def test_a_counter_that_goes_down_is_refused_without_counting_anything(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(100, 100), bob=(1, 1)))
    with pytest.raises(ServiceError) as raised:
        service.traffic.record("main", _report(service, EPOCH_A, 2, bob=(9, 9), alice=(50, 200)))
    assert (raised.value.code, raised.value.status) == ("counter_regression", 422)
    assert _totals(service) == {"alice": (100, 100), "bob": (1, 1)}


def test_nodes_add_up_and_last_seen_moves_only_with_traffic(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(10, 10), bob=(0, 0)))
    service.traffic.record("edge", _report(service, EPOCH_B, 1, alice=(5, 5)))
    users = {item.username: item for item in service.traffic.summary().users}

    assert (users["alice"].uplink_bytes, users["alice"].downlink_bytes) == (15, 15)
    assert users["alice"].last_seen_at is not None
    assert users["bob"].last_seen_at is None and users["bob"].uplink_bytes == 0


def test_traffic_holds_usernames_and_counters_only(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(1, 2)))
    assert set(service.traffic.summary().users[0].model_dump()) == {
        "username",
        "uplink_bytes",
        "downlink_bytes",
        "last_seen_at",
    }
