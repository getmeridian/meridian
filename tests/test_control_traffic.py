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
from tests.support.control_service import drop_device_privacy_schema, drop_operations_schema, drop_token_expiry_schema

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
    assert service.traffic.node_totals() == {"main": 2110}


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


def test_removing_a_user_erases_only_their_accounting_evidence(service: ControlService) -> None:
    from meridian.control.models import PrivacySettings

    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    first = _report(service, EPOCH_A, 1, alice=(10, 20), bob=(1, 2))
    late = _report(service, EPOCH_A, 2, alice=(100, 200), bob=(3, 4))
    alice = service.get_user("alice")
    assert alice is not None
    service.record_stats("main", first)
    service.record_stats("edge", _report(service, EPOCH_B, 1, alice=(5, 6)))

    service.delete_user("alice")

    for table in ("traffic_totals", "traffic_counters", "traffic_daily"):
        assert not service.traffic._store.rows(f"SELECT * FROM {table} WHERE user_id = ?", (alice.user_id,))
    assert len(service.traffic._store.rows("SELECT * FROM traffic_reports")) == 2
    assert service.traffic._store.rows("SELECT * FROM node_traffic_daily")
    service.record_stats("main", late)
    service.traffic.record("edge", late)
    for table in ("traffic_totals", "traffic_counters", "traffic_daily"):
        assert not service.traffic._store.rows(f"SELECT * FROM {table} WHERE user_id = ?", (alice.user_id,))
    assert _totals(service) == {"bob": (6, 8)}
    recreated = service.put_user("alice")
    assert recreated.user_id != alice.user_id
    assert recreated.used_traffic_bytes == recreated.lifetime_traffic_bytes == 0


def test_removing_a_user_rolls_back_their_accounting_with_the_user(
    service: ControlService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from meridian.control.models import PrivacySettings

    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, EPOCH_A, 1, alice=(10, 20)))
    tables = ("users", "traffic_totals", "traffic_counters", "traffic_reports", "traffic_daily", "node_traffic_daily")
    before = {table: [tuple(row) for row in service.traffic._store.rows(f"SELECT * FROM {table}")] for table in tables}

    def refused() -> None:
        raise RuntimeError("composition refused")

    monkeypatch.setattr(service, "_compose_all", refused)
    with pytest.raises(RuntimeError, match="composition refused"):
        service.delete_user("alice")

    assert {
        table: [tuple(row) for row in service.traffic._store.rows(f"SELECT * FROM {table}")] for table in tables
    } == before


@pytest.mark.parametrize("report_first", [True, False], ids=["report-holds-lock", "delete-holds-lock"])
def test_a_report_racing_user_removal_cannot_leave_orphan_accounting(
    service: ControlService, report_first: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from meridian.control.models import PrivacySettings

    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    report = _report(service, EPOCH_A, 1, alice=(10, 20), bob=(1, 2))
    alice = service.get_user("alice")
    assert alice is not None
    entered, release, waiting = Event(), Event(), Event()

    def hold() -> None:
        entered.set()
        assert release.wait(5)

    def first() -> None:
        with service.conditions(hold):
            if report_first:
                service.record_stats("main", report)
            else:
                service.delete_user("alice")

    def second() -> None:
        waiting.set()
        if report_first:
            service.delete_user("alice")
        else:
            service.record_stats("main", report)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_done = executor.submit(first)
        try:
            assert entered.wait(5)
            second_done = executor.submit(second)
            assert waiting.wait(5)
        finally:
            release.set()
        first_done.result(timeout=5)
        second_done.result(timeout=5)

    assert service.get_user("alice") is None
    for table in ("traffic_totals", "traffic_counters", "traffic_daily"):
        assert not service.traffic._store.rows(f"SELECT * FROM {table} WHERE user_id = ?", (alice.user_id,))
    assert _totals(service) == {"bob": (1, 2)}
    assert service.record_stats("main", report).acked_seq == 1


def test_node_totals_never_fall_when_an_old_epoch_arrives_late(service: ControlService) -> None:
    service.record_stats("main", _report(service, EPOCH_A, 1, alice=(10, 90)))
    service.record_stats("main", _report(service, EPOCH_B, 1, alice=(100, 800)))
    assert service.traffic.node_totals() == {"main": 1000}

    old = _report(service, EPOCH_A, 2, alice=(20, 90))
    service.record_stats("main", old)
    assert service.traffic.node_totals() == {"main": 1010}
    service.record_stats("main", old)
    assert service.traffic.node_totals() == {"main": 1010}
    assert sum(_totals(service)["alice"]) == 1010


def test_node_totals_outlive_baseline_retention_user_removal_and_history_off(service: ControlService) -> None:
    from meridian.control.models import PrivacySettings

    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    count = KEPT_EPOCHS + REMEMBERED_RETIRED_EPOCHS + 5
    for index in range(count):
        service.record_stats("main", _report(service, f"{index:08x}-6c1d-4e8b-9a7f-2b5d4c3e1f00", 1, alice=(2, 3)))
    service.delete_user("alice")
    service.set_privacy(PrivacySettings(), author="root")
    service.delete_node("main")
    assert service.traffic.node_totals() == {"main": count * 5}


def test_first_traffic_and_last_node_change_only_with_accepted_growth(service: ControlService) -> None:
    first, second, third = "2026-11-01T12:00:00Z", "2026-11-01T12:01:00Z", "2026-11-01T12:02:00Z"
    unused = service.get_user("alice")
    assert unused is not None
    assert unused.first_traffic_at is unused.last_traffic_node is None
    report = _report(service, EPOCH_A, 1, alice=(10, 20))
    service.record_stats("main", report, now=first)
    service.record_stats("edge", _report(service, EPOCH_B, 1, alice=(2, 3)), now=second)
    service.record_stats("main", report, now=third)
    service.record_stats("main", _report(service, EPOCH_A, 2, alice=(10, 20)), now=third)
    user = service.get_user("alice")
    assert user is not None
    assert (user.first_traffic_at, user.online_at, user.last_traffic_node) == (first, second, "edge")
    service.reset_user_traffic("alice")
    service.reissue_user("alice")
    assert service.get_user("alice").first_traffic_at == first
    assert service.get_user("alice").last_traffic_node == "edge"


def test_node_activity_is_growth_in_the_latest_report_window_and_replays_leave_it_alone(
    service: ControlService,
) -> None:
    from meridian.control.models import NodeSpec

    service.put_node_spec("main", NodeSpec(resources=[]))
    assert service.node_status("main").active_users is None
    first = _report(service, EPOCH_A, 1, alice=(10, 20), bob=(0, 0))
    service.record_stats("main", first, now="2026-11-01T12:00:01Z")
    status = service.node_status("main")
    assert (status.active_users, status.active_report_at, status.active_report_received_at) == (
        1,
        first.collected_at,
        "2026-11-01T12:00:01Z",
    )
    service.record_stats("main", first, now="2026-11-01T12:00:02Z")
    assert service.node_status("main").active_report_received_at == "2026-11-01T12:00:01Z"
    empty = _report(service, EPOCH_A, 2, alice=(10, 20)).model_copy(update={"collected_at": "2026-11-01T12:01:00Z"})
    service.record_stats("main", empty, now="2026-11-01T12:01:01Z")
    assert service.node_status("main").active_users == 0
    # Accepted growth from an older collected window still counts bytes, but cannot replace newer activity.
    service.record_stats("main", _report(service, EPOCH_B, 1, bob=(1, 2)), now="2026-11-01T12:02:00Z")
    assert service.node_status("main").active_users == 0
    assert service.node_status("main").active_report_at == empty.collected_at


def test_refused_growth_rolls_back_node_totals_and_activity_with_user_accounting(service: ControlService) -> None:
    from meridian.control.models import NodeSpec

    service.put_node_spec("main", NodeSpec(resources=[]))
    service.record_stats("main", _report(service, EPOCH_A, 1, alice=(100, 100), bob=(1, 1)))
    before = service.node_status("main")
    with pytest.raises(ServiceError, match="went down"):
        service.record_stats("main", _report(service, EPOCH_A, 2, bob=(9, 9), alice=(50, 200)))
    assert service.traffic.node_totals() == {"main": 202}
    assert service.node_status("main") == before
    assert _totals(service) == {"alice": (100, 100), "bob": (1, 1)}


@pytest.mark.parametrize("old_version", [11, 12], ids=["from-eleven", "from-twelve"])
def test_schema_thirteen_backfills_retained_node_evidence_once_and_marks_the_accounting_start(
    tmp_path: Path, old_version: int
) -> None:
    from datetime import datetime

    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    for index in range(KEPT_EPOCHS + 2):
        service.record_stats("main", _report(service, f"{index:08x}-6c1d-4e8b-9a7f-2b5d4c3e1f00", 1, alice=(4, 6)))
    service.close()
    with sqlite3.connect(path) as db:
        drop_operations_schema(db)
        if old_version < 12:
            drop_token_expiry_schema(db)
        db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(old_version),))
    since = None
    for _ in range(2):
        service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
        assert service.traffic.node_totals() == {"main": KEPT_EPOCHS * 10}
        row = service._store.row("SELECT * FROM node_traffic_totals WHERE node_id = 'main'")
        assert datetime.fromisoformat(row["counting_since"]).tzinfo is not None
        assert since is None or since == row["counting_since"]
        since = row["counting_since"]
        assert service.get_user("alice").first_traffic_at is None
        assert service.get_user("alice").last_traffic_node is None
        assert service.get_user("alice").lifetime_traffic_bytes == (KEPT_EPOCHS + 2) * 10
        assert service._store.meta("schema_version") == "13"
        service.close()
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        epoch = f"{KEPT_EPOCHS + 1:08x}-6c1d-4e8b-9a7f-2b5d4c3e1f00"
        service.record_stats("main", _report(service, epoch, 2, alice=(5, 6)), now="2026-11-01T12:00:00Z")
        assert service.get_user("alice").first_traffic_at is None
        assert service.get_user("alice").last_traffic_node == "main"
    finally:
        service.close()


def test_imported_use_without_a_first_time_stays_unknown_when_more_traffic_arrives(service: ControlService) -> None:
    from meridian.control.models import UserImport, UserImportBatch

    service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username="carried",
                    uuid=EPOCH_A,
                    sub_token="Zq8xT3vNw2LmK7pR",
                    used_traffic_bytes=100,
                    lifetime_traffic_bytes=100,
                )
            ]
        )
    )
    service.record_stats("edge", _report(service, EPOCH_B, 1, carried=(3, 5)))
    user = service.get_user("carried")
    assert user.first_traffic_at is None
    assert user.last_traffic_node == "edge" and user.lifetime_traffic_bytes == 108
