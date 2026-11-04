"""Capacity history is bounded, replay-safe, and independent of person privacy levels."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.errors import ServiceError
from meridian.control.models import NodeSpec
from meridian.control.schema import migrate
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.store import Store
from meridian.core.agent_protocol import ObservedStateReport
from tests.support.capacity import NOW, capacity_report
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.support.token_service import Clock, serve_tokens, token_request


@pytest.fixture
def clock() -> Clock:
    result = Clock()
    result.now = datetime.fromisoformat(NOW).timestamp()
    return result


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> ControlService:
    result = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=clock,
    )
    result.put_node_spec("main", NodeSpec(resources=[]))
    return result


def test_older_agents_report_no_capacity_instead_of_zero(service: ControlService) -> None:
    status = service.node_status("main")
    assert status.capacity.status == "not_reported"
    assert status.capacity.sample is None


def test_capacity_zero_is_current_and_hellos_do_not_refresh_it(service: ControlService) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    raw = capacity_report(load1_milli=0, memory_available_bytes=0)
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    current = service.capacity.current("main")
    assert current.status == "current"
    assert current.sample.load1_milli == current.sample.memory_available_bytes == 0
    now = datetime.fromisoformat(NOW).timestamp() + 181
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    stale = service.capacity.current("main", now=now)
    assert stale.status == "stale" and stale.received_at == NOW


def test_capacity_rollups_survive_restart_without_person_history(
    service: ControlService, tmp_path: Path, clock: Clock
) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    raw = capacity_report()
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    view = service.capacity.view("main")
    assert len(view.recent) == 1
    assert len(view.hourly) == 1 and view.hourly[0].samples == 1
    assert service.privacy().usage_history == "off"
    restored = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    assert restored.capacity.view("main").hourly == view.hourly
    assert restored.capacity.view("main").recent == []
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)


def receive(service: ControlService, **changes: object) -> None:
    raw = capacity_report(**changes)
    service.report("main", ObservedStateReport.model_validate(raw), raw)


def test_replays_and_retired_collection_epochs_never_refresh_capacity(service: ControlService, clock: Clock) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    receive(service)
    clock.now += 30
    receive(service, sequence=2, load1_milli=0)
    accepted = service.capacity.current("main")
    clock.now += 100
    receive(service, sequence=2, load1_milli=9000)
    receive(service, sequence=1, load1_milli=9000)
    assert service.capacity.current("main").received_at == accepted.received_at
    assert service.capacity.current("main").values.load1_milli == 0
    assert service.capacity.view("main").hourly[0].samples == 2
    receive(service, collection_epoch="66a5b659-5aad-425c-a786-512ad6a86ac1", load1_milli=500)
    accepted = service.capacity.current("main")
    clock.now += 100
    receive(service, sequence=3, load1_milli=9000)
    assert service.capacity.current("main").received_at == accepted.received_at
    assert service.capacity.current("main").values.load1_milli == 500


def test_capacity_series_and_hourly_retention_are_bounded(
    service: ControlService, clock: Clock, tmp_path: Path
) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    for sequence in range(1, 402):
        receive(service, sequence=sequence)
        clock.now += 10
    assert 350 <= len(service.capacity.view("main").recent) <= 360
    for sequence in range(402, 602):
        receive(service, sequence=sequence)
        clock.now += 3600
    assert len(service.capacity.view("main", hours=168).hourly) <= 168
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT count(*) FROM node_capacity_hourly").fetchone()[0] <= 168
        assert db.execute("SELECT count(*) FROM node_capacity_state").fetchone()[0] == 1
    clock.now += 8 * 86400
    service.enforce()
    assert service.capacity.view("main", hours=168).hourly == []


def test_no_capacity_and_unknown_rates_are_not_zero(service: ControlService) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    assert service.capacity.current("main").status == "unavailable"
    receive(
        service,
        cpu_count=None,
        load1_milli=None,
        load5_milli=None,
        load15_milli=None,
        memory_total_bytes=None,
        memory_available_bytes=None,
        network=None,
        xray=None,
    )
    assert service.capacity.current("main").status == "unavailable"
    assert service.capacity.view("main").last_day.rx_bytes is None


def test_adjacent_zero_traffic_is_measured_and_counter_regression_rolls_back(
    service: ControlService, clock: Clock
) -> None:
    from tests.support.capacity import NETWORK_EPOCH

    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    receive(service)
    clock.now += 60
    network = {
        "counter_epoch": NETWORK_EPOCH,
        "rx_bytes": 0,
        "tx_bytes": 0,
        "rx_bytes_per_second": 0,
        "tx_bytes_per_second": 0,
        "interval_millis": 60_000,
    }
    receive(service, sequence=2, network=network)
    day = service.capacity.view("main").last_day
    assert day.covered_seconds == 60 and day.rx_bytes == day.tx_bytes == 0
    clock.now += 60
    receive(service, sequence=3, network={**network, "rx_bytes": 1000, "rx_bytes_per_second": 16})
    before = service.capacity.view("main")
    with pytest.raises(ServiceError, match="counter epoch"):
        receive(service, sequence=4, network=network)
    assert service.capacity.view("main") == before


@pytest.mark.parametrize("query", ["hours=0", "hours=169", "hours=bad", "hours=1&hours=2", "extra=1"])
def test_capacity_queries_fail_with_plain_validation_errors(tmp_path: Path, query: str) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", NodeSpec(resources=[]))
        status, answer = token_request(fleet.url + "/api/nodes/main/capacity?" + query, token=ADMIN_TOKEN)
        assert status == 400 and answer["error"]["code"] == "invalid_request"


def test_capacity_migration_is_one_self_contained_step_from_22(tmp_path: Path) -> None:
    store = Store(tmp_path / "migration.db")
    store.create(
        "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY);"
        "CREATE TABLE users(user_id TEXT PRIMARY KEY, username TEXT);"
        "CREATE TABLE revisions(number INTEGER PRIMARY KEY, status TEXT, intent TEXT);"
        "CREATE TABLE people_versions(user_id TEXT PRIMARY KEY, version INTEGER NOT NULL);"
    )
    with store.transaction():
        store.set_meta("schema_version", "22")
        migrate(store)
    assert store.meta("schema_version") == str(SCHEMA_VERSION)
    assert {
        row["name"]
        for row in store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'node_capacity_%'")
    } == {
        "node_capacity_state",
        "node_capacity_hourly",
    }
    store.close()


def test_network_rollups_split_utc_boundaries_and_do_not_count_resets(service: ControlService, clock: Clock) -> None:
    from tests.support.capacity import NETWORK_EPOCH

    clock.now -= 30
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    receive(service)
    clock.now += 60
    network = {
        "counter_epoch": NETWORK_EPOCH,
        "rx_bytes": 6000,
        "tx_bytes": 3000,
        "rx_bytes_per_second": 100,
        "tx_bytes_per_second": 50,
        "interval_millis": 60_000,
    }
    receive(service, sequence=2, network=network)
    view = service.capacity.view("main")
    assert [hour.rx_bytes for hour in view.hourly] == [3000, 3000]
    assert [hour.covered_seconds for hour in view.hourly] == [30, 30]
    assert view.last_day.rx_bytes == 6000 and view.last_day.tx_bytes == 3000
    clock.now += 60
    receive(
        service,
        sequence=3,
        network={
            **network,
            "counter_epoch": "00000000-81cd-483d-a4b8-706fd151a024",
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_bytes_per_second": None,
            "tx_bytes_per_second": None,
            "interval_millis": None,
        },
    )
    assert service.capacity.view("main").last_day.rx_bytes == 6000


def test_capacity_survives_the_cli_fleet_adapter(tmp_path: Path) -> None:
    from meridian.control_access import ServiceFleetPanel
    from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel, build_fleet_status

    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", NodeSpec(resources=[]))
        fleet.service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
        receive(fleet.service, load1_milli=0)
        with ServiceFleetPanel(fleet.url, ADMIN_TOKEN, node_ids=["main"], endpoint_ids=[]) as panel:
            nodes = panel.list_nodes()
        assert nodes[0].capacity == fleet.service.node_status("main").capacity
        topology = FleetTopology(
            panel=TopologyPanel(
                url=fleet.url, display_url="", server_ip="198.51.100.10", ssh_user="root", ssh_port=22, deployed_with=""
            ),
            nodes=[
                TopologyNode(
                    ip="198.51.100.10",
                    name="main",
                    uuid="main",
                    is_panel_host=True,
                    ssh_user="root",
                    ssh_port=22,
                    domain="",
                    sni="",
                    xhttp_path="",
                    ws_path="",
                )
            ],
        )
        result = build_fleet_status(topology, panel_healthy=True, api_nodes=nodes)
        assert result.nodes[0].capacity.status == "current"
        assert result.to_data()["nodes"][0]["capacity"]["values"]["load1_milli"] == 0


def test_rollup_bytes_can_cross_the_agent_integer_ceiling_across_epochs(service: ControlService, clock: Clock) -> None:
    from tests.support.capacity import NETWORK_EPOCH

    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    receive(service)
    amount = 5_000_000_000_000_000
    counter = {
        "counter_epoch": NETWORK_EPOCH,
        "rx_bytes": amount,
        "tx_bytes": 0,
        "rx_bytes_per_second": amount // 180,
        "tx_bytes_per_second": 0,
        "interval_millis": 180_000,
    }
    clock.now += 180
    receive(service, sequence=2, network=counter)
    clock.now += 10
    epoch = "00000000-81cd-483d-a4b8-706fd151a024"
    receive(
        service,
        sequence=3,
        network={
            **counter,
            "counter_epoch": epoch,
            "rx_bytes": 0,
            "rx_bytes_per_second": None,
            "tx_bytes_per_second": None,
            "interval_millis": None,
        },
    )
    clock.now += 180
    receive(service, sequence=4, network={**counter, "counter_epoch": epoch})
    assert service.capacity.view("main").last_day.rx_bytes == 2 * amount


def test_partial_memory_evidence_remains_available(service: ControlService) -> None:
    service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    receive(
        service,
        cpu_count=None,
        load1_milli=None,
        load5_milli=None,
        load15_milli=None,
        memory_total_bytes=None,
        memory_available_bytes=4096,
        network=None,
        xray=None,
    )
    current = service.capacity.current("main")
    assert current.status == "current"
    assert current.values.memory_available_bytes == 4096
