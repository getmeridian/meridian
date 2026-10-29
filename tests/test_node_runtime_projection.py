"""Reachability, runtime evidence, and convergence remain separate across surfaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from meridian.cluster import ClusterConfig
from meridian.commands import fleet
from meridian.control.client import ControlClient
from meridian.control.models import NodeSpec
from meridian.control.service import ControlService
from meridian.control.ui_views import fleet_view
from meridian.control_access import ServiceFleetPanel
from meridian.core.agent_protocol import ObservedStateReport
from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.support.node_runtime import node_report


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    return ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")


def test_node_status_and_console_keep_the_report_health_and_versions(service: ControlService) -> None:
    service.put_node_spec("main", NodeSpec(resources=[]))
    raw = node_report(datetime.now(UTC).isoformat().replace("+00:00", "Z"), health="unhealthy", check="fail")
    service.report("main", ObservedStateReport.model_validate(raw), raw)

    status = service.node_status("main").model_dump(mode="json")
    assert status["runtime"] == {key: raw[key] for key in ("health", "versions", "observed_at")}
    server = fleet_view(service).servers[0].model_dump(mode="json")
    assert server["runtime"] == status["runtime"]
    assert server["runtime_available"] is True


@pytest.mark.parametrize(
    ("health", "check", "age", "live", "exit_code"),
    [
        ("healthy", "pass", 0, True, 0),
        ("unhealthy", "pass", 0, True, 4),
        ("healthy", "fail", 0, True, 4),
        ("degraded", "warn", 0, True, 4),
        ("unhealthy", "fail", 90, True, 3),
        ("healthy", "pass", 90, False, 3),
    ],
    ids=["healthy", "unhealthy", "failing-check", "degraded", "old-report-live-polls", "unseen"],
)
def test_fleet_status_judges_fresh_runtime_evidence(
    service: ControlService, health: str, check: str, age: int, live: bool, exit_code: int
) -> None:
    service.put_node_spec("main", NodeSpec(resources=[]))
    raw = node_report(
        (datetime.now(UTC) - timedelta(seconds=age)).isoformat().replace("+00:00", "Z"), health=health, check=check
    )
    current = service.node_status("main")
    raw["applied"] = {"fleet_epoch": 1, "revision": current.revision, "sha256": current.sha256}
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    status = service.node_status("main")
    if not live:
        status = status.model_copy(update={"last_seen_at": raw["observed_at"]})
    topology = FleetTopology(
        panel=TopologyPanel(
            url="https://198.51.100.10/",
            display_url="",
            server_ip="198.51.100.10",
            ssh_user="root",
            ssh_port=22,
            deployed_with="",
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
    with (
        patch.object(ControlClient, "node", return_value=status),
        patch.object(ControlClient, "health"),
        patch.object(ControlClient, "list_users", return_value=[]),
        patch.object(fleet, "load_topology_cluster", return_value=ClusterConfig()),
        patch.object(fleet, "topology_from_local_cluster", return_value=topology),
        patch.object(
            fleet,
            "fleet_panel",
            return_value=ServiceFleetPanel(
                "https://198.51.100.10/",
                ADMIN_TOKEN,
                node_ids=["main"],
                endpoint_ids=[],
            ),
        ),
    ):
        assert fleet.run_status() == exit_code
