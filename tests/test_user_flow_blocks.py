"""Suspension receipts distinguish live blocking from unacknowledged or old nodes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import AGENT_SEEN_SECONDS, NodeSpec, UserUpdate
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.core.agent_protocol import (
    NodeHealth,
    NodeVersions,
    ObservedStateReport,
    ResourceError,
    StatsReport,
    UserFlowBlocking,
    UserTraffic,
)
from meridian.core.agent_types import NodeStateRef

NOW = "2026-11-01T12:00:00Z"
FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    monkeypatch.setattr("meridian.control.traffic.utc_now", lambda: NOW)
    with_service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    spec = NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "core-config",
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "api_address": "127.0.0.1:10085",
                    "config": {
                        "api": {"services": ["HandlerService", "StatsService", "RoutingService"]},
                        "inbounds": [{"tag": "vless", "protocol": "vless"}, {"tag": "hy2", "protocol": "hysteria2"}],
                    },
                }
            ],
            "managed_inbounds": [
                {"tag": "vless", "protocol": "vless", "flow": ""},
                {"tag": "hy2", "protocol": "hysteria2", "flow": ""},
            ],
        }
    )
    with_service.put_node_spec("main", spec)
    yield with_service
    with_service.close()


@pytest.mark.parametrize("reason", ["disabled", "expired", "limited"])
def test_automatic_suspension_records_a_pending_node_without_resetting_shared_inbounds(
    service: ControlService, reason: str
):
    user = service.put_user("alice")
    other = service.put_user("bob")
    if reason == "disabled":
        service.update_user("alice", UserUpdate(status="disabled"))
    elif reason == "expired":
        service.update_user("alice", UserUpdate(expire_at="2026-11-01T12:01:00Z"))
        service.enforce("2026-11-01T12:02:00Z")
    else:
        service.update_user("alice", UserUpdate(traffic_limit_bytes=100))
        service.record_stats(
            "main",
            StatsReport(
                counter_epoch="0f1e2d3c-4b5a-4968-8776-655443322110",
                seq=1,
                collected_at=NOW,
                users=[UserTraffic(user_id=user.user_id, uplink_bytes=0, downlink_bytes=100)],
            ),
            now=NOW,
        )
    state = service.user_flow_blocks("alice")
    assert len(state.nodes) == 1
    assert (state.nodes[0].status, state.nodes[0].reason) == ("pending_node", reason)
    with service._lock:
        document = json.loads(service._db.execute("SELECT document FROM nodes WHERE node_id = 'main'").fetchone()[0])
        assert service._db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0
    carried = [
        client["user_id"]
        for resource in document["resources"]
        if resource["kind"] in {"xray_users", "hysteria2_users"}
        for client in resource["users"]
    ]
    assert carried == [other.user_id, other.user_id]


def acknowledge(service: ControlService, *, ready: bool = True, old_agent: bool = False) -> None:
    status = service.node_status("main")
    ref = NodeStateRef(fleet_epoch=status.fleet_epoch, revision=status.revision, sha256=status.sha256)
    report = ObservedStateReport(
        fleet_id=service.fleet_id,
        node_id="main",
        applied=ref,
        target=ref,
        resources=[],
        observed_at=NOW,
        health=NodeHealth(
            status="healthy", checks=[], uptime_seconds=1, load1_milli=0, memory_available_bytes=1, disk_free_bytes=1
        ),
        versions=NodeVersions(
            agent="0.2.11", xray="26.7.28", realm=None, nginx=None, os_id="debian", os_version="13", kernel="6.1"
        ),
        user_flow_blocking=None
        if old_agent
        else UserFlowBlocking(
            document=ref,
            status="ready" if ready else "pending",
            error=None
            if ready
            else ResourceError(code="xray_api_failed", message="The routing API is unavailable.", retryable=True),
        ),
    )
    service.report("main", report, report.model_dump(mode="json"))


@pytest.mark.parametrize("outcome", ["blocked", "pending_node", "agent_too_old"])
def test_reports_record_only_negotiated_current_node_outcomes_without_credentials(
    service: ControlService, outcome: str
):
    user = service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=[] if outcome == "agent_too_old" else ["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service, ready=outcome == "blocked", old_agent=outcome == "agent_too_old")
    state = service.user_flow_blocks("alice")
    assert state.nodes[0].status == outcome
    events = [event for event in service.events.list(limit=100).events if event.kind == "user.flow_blocking"]
    assert events[-1].detail.status == outcome
    encoded = json.dumps([event.to_data() for event in events])
    assert user.uuid not in encoded and user.sub_token not in encoded
    assert "routing API" not in encoded and "client_ip" not in encoded and "device" not in encoded


def test_reenable_removes_receipts_for_the_same_generation(service: ControlService):
    user = service.put_user("alice")
    service.update_user("alice", UserUpdate(status="disabled"))
    assert service.user_flow_blocks("alice").nodes
    returned = service.update_user("alice", UserUpdate(status="active"))
    assert returned.credential_generation == user.credential_generation
    assert service.user_flow_blocks("alice").nodes == []


def test_disable_enable_after_reissue_keeps_the_older_pending_generation(service: ControlService):
    before = service.put_user("alice")
    reissued = service.reissue_user("alice")
    assert reissued.credential_generation == 2
    service.update_user("alice", UserUpdate(status="disabled"))
    service.update_user("alice", UserUpdate(status="active"))
    receipts = service.user_flow_blocks("alice").nodes
    assert len(receipts) == 1
    assert receipts[0].credential_generation == before.credential_generation
    assert receipts[0].status == "pending_node"


def test_stale_reports_or_a_new_epoch_never_claim_blocked_and_reads_write_nothing(service: ControlService, monkeypatch):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    assert service.user_flow_blocks("alice").nodes[0].status == "blocked"
    before = service._db.total_changes
    stamp = datetime.fromisoformat(NOW).timestamp()
    monkeypatch.setattr(service.flow_blocks, "_clock", lambda: stamp + AGENT_SEEN_SECONDS)
    assert service.user_flow_blocks("alice").nodes[0].status == "pending_node"
    assert service._db.total_changes == before
    monkeypatch.setattr(service.flow_blocks, "_clock", lambda: stamp)
    with service._store.transaction():
        service._store.set_meta("fleet_epoch", "2")
    assert service.user_flow_blocks("alice").nodes[0].status == "pending_node"


def test_schema_nineteen_gains_one_generation_and_receipts_step(tmp_path: Path):
    import sqlite3

    path = tmp_path / "old.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("alice")
    service.close()
    with sqlite3.connect(path) as database:
        from tests.support.control_service import drop_people_query_schema

        drop_people_query_schema(database)
        database.execute("UPDATE meta SET value = '19' WHERE key = 'schema_version'")
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        current = restored.get_user("alice")
        assert current.credential_generation == 1
        assert (current.user_id, current.uuid, current.sub_token) == (user.user_id, user.uuid, user.sub_token)
        assert restored._store.meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        restored.close()
