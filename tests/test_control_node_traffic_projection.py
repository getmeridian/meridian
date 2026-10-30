"""Fleet node traffic comes from the service's totals, including retried reports."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from meridian.control.client import ControlClient
from meridian.control.models import NodeSpec
from meridian.control.service import ControlService, utc_now
from meridian.control_access import ServiceFleetPanel
from meridian.core.agent_protocol import StatsReport
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID


def test_fleet_adapter_keeps_node_traffic_from_the_service(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_node_spec("main", NodeSpec(resources=[]))
    user = service.put_user("sample")
    stats = StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
            "seq": 1,
            "collected_at": utc_now(),
            "users": [{"user_id": user.user_id, "uplink_bytes": 40, "downlink_bytes": 70}],
        }
    )
    service.record_stats("main", stats)
    service.record_stats("main", stats)
    assert service.traffic.node_totals() == {"main": 110}
    with (
        patch.object(ControlClient, "node", side_effect=lambda node_id: service.node_status(node_id)),
        ServiceFleetPanel("https://198.51.100.10/", ADMIN_TOKEN, node_ids=["main"], endpoint_ids=[]) as panel,
    ):
        (node,) = panel.list_nodes()
    assert node.traffic_used == 110
