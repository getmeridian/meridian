"""Revocation evidence follows the required revision and a fresh received report."""

import json

from meridian.control.models import UserUpdate
from meridian.control.service import ControlService
from meridian.core.agent_protocol import ObservedStateReport
from tests.test_user_flow_blocks import acknowledge
from tests.test_user_flow_blocks import service as service


def flow_events(service: ControlService):
    return [event for event in service.events.list().events if event.kind == "user.flow_blocking"]


def test_a_fresh_hourly_hello_preserves_blocking_evidence_and_emits_no_transition(service: ControlService):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    events = flow_events(service)
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    assert service.user_flow_blocks("alice").nodes[0].status == "blocked"
    assert flow_events(service) == events


def test_unrelated_revisions_keep_the_receipt_bound_to_the_revocation_revision(service: ControlService):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    raw = json.loads(service._store.row("SELECT report FROM nodes WHERE node_id = 'main'")["report"])
    events = flow_events(service)
    required = service.user_flow_blocks("alice").nodes[0].required
    service.put_user("bob")
    assert service.node_status("main").revision > required.revision
    assert service.user_flow_blocks("alice").nodes[0].status == "blocked"
    report = ObservedStateReport.model_validate(raw)
    service.report("main", report, raw)
    assert service.user_flow_blocks("alice").nodes[0].status == "blocked"
    assert flow_events(service) == events


def test_a_newer_epoch_cannot_confirm_an_older_epoch_receipt(service: ControlService):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    with service._store.transaction():
        service._store.set_meta("fleet_epoch", "2")
    acknowledge(service)
    assert service.user_flow_blocks("alice").nodes[0].status == "pending_node"


def test_a_hello_does_not_refresh_the_age_of_the_last_runtime_report(service: ControlService, monkeypatch):
    from datetime import datetime, timedelta

    from meridian.control.models import AGENT_SEEN_SECONDS
    from tests.test_user_flow_blocks import NOW

    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    later = datetime.fromisoformat(NOW) + timedelta(seconds=AGENT_SEEN_SECONDS + 1)
    monkeypatch.setattr(service.flow_blocks, "_clock", lambda: later.timestamp())
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: later.isoformat())
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    assert service.user_flow_blocks("alice").nodes[0].status == "pending_node"


def test_equal_revision_evidence_must_match_the_revocation_hash(service: ControlService):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    raw = json.loads(service._store.row("SELECT report FROM nodes WHERE node_id = 'main'")["report"])
    raw["applied"]["sha256"] = raw["user_flow_blocking"]["document"]["sha256"] = "0" * 64
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    assert service.user_flow_blocks("alice").nodes[0].status == "pending_node"


def test_epoch_recomposition_requires_fresh_evidence_for_the_new_epoch(service: ControlService):
    service.put_user("alice")
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    service.update_user("alice", UserUpdate(status="disabled"))
    acknowledge(service)
    with service._store.transaction():
        service._store.set_meta("fleet_epoch", "2")
        service._compose_all()
    receipt = service.user_flow_blocks("alice").nodes[0]
    assert receipt.required.fleet_epoch == 2 and receipt.status == "pending_node"
    acknowledge(service)
    assert service.user_flow_blocks("alice").nodes[0].status == "blocked"
