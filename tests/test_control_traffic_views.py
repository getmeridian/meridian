"""Aggregate accounting evidence reaches the person API, console data, and human/JSON CLI views."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from meridian.adapters.cluster import topology_from_cluster
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands import client, fleet
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import NodeSpec
from meridian.control.service import ControlService, utc_now
from meridian.core.agent_protocol import ObservedStateReport, StatsReport
from meridian.i18n import set_locale
from meridian.servers import ServerEntry
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.node_runtime import node_report

NOW = "2026-11-01T12:00:00Z"


@pytest.mark.parametrize("json_mode", [False, True], ids=["human", "json"])
def test_traffic_evidence_reaches_client_show_and_fleet_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("alice")
    service.put_node_spec("main", NodeSpec(resources=[]))
    current = service.node_status("main")
    runtime_report = node_report(utc_now())
    runtime_report["applied"] = {
        "fleet_epoch": current.fleet_epoch,
        "revision": current.revision,
        "sha256": current.sha256,
    }
    service.report("main", ObservedStateReport.model_validate(runtime_report), runtime_report)
    report = StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
            "seq": 1,
            "collected_at": NOW,
            "users": [{"user_id": user.user_id, "uplink_bytes": 10, "downlink_bytes": 20}],
        }
    )
    service.record_stats("main", report, now=NOW)
    service.close()
    set_locale("en")
    set_json_mode(json_mode)
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as api:
            person = api.get_user("alice")
            assert (person.first_traffic_at, person.last_traffic_node) == (NOW, "main")
            node = api.node("main")
            assert node.traffic_bytes == 30
            assert (node.active_users, node.active_report_at, node.traffic_counting_since) == (1, NOW, NOW)
            assert node.runtime_available() and node.in_sync
            console = httpx.get(url + "/ui/api/fleet", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}).json()
            assert console["servers"][0]["traffic_bytes"] == 30
            assert console["servers"][0]["active_users"] == 1
            assert console["servers"][0]["active_report_at"] == NOW
            assert console["servers"][0]["runtime_available"] is True
            assert console["servers"][0]["runtime"]["health"] == runtime_report["health"]
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.10"),
                topology_intent=single_server_intent(None, server_ref="srv-a", client_name="alice"),
            )
            topology = topology_from_cluster(cluster, servers=[ServerEntry(host="198.51.100.10", id="srv-a")])
            monkeypatch.setattr(client, "load_topology_cluster", lambda: cluster)
            monkeypatch.setattr(fleet, "load_topology_cluster", lambda: cluster)
            monkeypatch.setattr(fleet, "topology_from_local_cluster", lambda _cluster: topology)
            client.run_show("alice")
            output = capsys.readouterr()
            if json_mode:
                detail = json.loads(output.out)["data"]["client"]
                assert (detail["first_traffic_at"], detail["last_traffic_node"]) == (NOW, "main")
            else:
                assert "First reported traffic" in output.err and NOW in output.err
                assert "Last reporting node" in output.err and "main" in output.err
            fleet.run_status()
            output = capsys.readouterr()
            if json_mode:
                status = json.loads(output.out)["data"]["nodes"][0]
                assert (status["active_users"], status["active_report_at"]) == (1, NOW)
                assert status["traffic_bytes"] == 30
                assert status["live"] and status["runtime_available"] and status["in_sync"]
                assert status["runtime"]["health"] == runtime_report["health"]
            else:
                assert "active" in output.err and "Active in the last report" in output.err
                assert NOW in output.err
                assert "30 B" in output.err
                assert "Reachability: live" in output.err
                assert "Runtime: healthy" in output.err
                assert "Convergence: in sync" in output.err
    finally:
        set_json_mode(False)
