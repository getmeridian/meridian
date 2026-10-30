"""Signed commands: negotiation, durable delivery, revision gates, and safe results over loopback."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, ManagedResourceBinding, PanelConfig
from meridian.commands.client import run_remove
from meridian.console import set_json_mode
from meridian.control.backups import strip_unsnapshotted
from meridian.control.client import ControlClient, ControlServiceConflict
from meridian.control.command_models import NodeCommandOutcome, ReconcileCreate
from meridian.control.enrollment import token_sha256
from meridian.control.models import NodeSpec, TokenCreate
from meridian.control.server import ControlServer
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.tokens import ROOT
from meridian.core.agent_protocol import StatsReport
from meridian.core.agent_signing import COMMAND_CONTEXT, verify
from meridian.reconciler.meridian_drivers import ControlUserDriver
from meridian.remnawave import User
from tests.support.agent_node import FakeNode
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.support.node_commands import command_node_spec as spec
from tests.support.topology import single_exit_intent

COMMAND_KINDS = ["reconcile", "report", "reset_inbound", "restart_unit", "renew_credential"]
TAG = "vless-reality"


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def stamp(self) -> str:
        return datetime.fromtimestamp(self.now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> Iterator[ControlService]:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    yield service
    service.close()


@pytest.fixture
def http(service: ControlService) -> Iterator[str]:
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def ask(url: str, method: str = "GET", body: Any = None, token: str = ADMIN_TOKEN) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def session(http: str, clock: Clock, *, node_id: str = "main", capabilities: list[str] | None = None) -> str:
    assert ask(f"{http}/api/nodes/{node_id}", "PUT", spec().model_dump(mode="json"))[0] == 200
    status, grant = ask(f"{http}/api/nodes/{node_id}/enrollment", "POST", {})
    assert status == 200
    node = FakeNode(node_id)
    status, enrolled = ask(f"{http}/agent/v1/enroll", "POST", node.enroll_request(grant["enrollment_token"]), "")
    assert status == 200
    node.certificate_pem = enrolled["certificate_pem"]
    offered = ["long_poll", "systemd_units", "xray_config", *COMMAND_KINDS] if capabilities is None else capabilities
    status, welcome = ask(f"{http}/agent/v1/hello", "POST", node.hello(clock.stamp(), capabilities=offered), "")
    assert status == 200
    assert set(welcome["capabilities"]) == set(offered)
    return str(welcome["session_token"])


def poll(http: str, token: str, *, have: Any = None, wait: int = 0) -> dict[str, Any]:
    status, body = ask(f"{http}/agent/v1/poll", "POST", {"type": "poll", "have": have, "wait_seconds": wait}, token)
    assert status == 200, body
    return body


def report(document: dict[str, Any]) -> dict[str, Any]:
    reference = {key: document[key] for key in ("fleet_epoch", "revision", "sha256")}
    return {
        "type": "report",
        "fleet_id": FLEET_ID,
        "node_id": "main",
        "applied": reference,
        "target": reference,
        "resources": [],
        "health": {
            "status": "healthy",
            "checks": [],
            "uptime_seconds": 1,
            "load1_milli": 0,
            "memory_available_bytes": 1,
            "disk_free_bytes": 1,
        },
        "versions": {
            "agent": "0.2.8",
            "xray": None,
            "realm": None,
            "nginx": None,
            "os_id": "debian",
            "os_version": "13",
            "kernel": "6.1.0",
        },
        "observed_at": "2026-10-30T12:00:00Z",
    }


def issue(http: str, body: dict[str, Any]) -> dict[str, Any]:
    status, record = ask(f"{http}/api/nodes/main/commands", "POST", body)
    assert status == 200, record
    return record


def test_commands_are_signed_oldest_first_and_redelivered_until_a_result_is_acked(
    http: str, service: ControlService, clock: Clock
) -> None:
    token = session(http, clock)
    first = issue(http, {"kind": "restart_unit", "resource_id": "unit:netcore"})
    second = issue(http, {"kind": "report", "include_stats": True})
    commands = poll(http, token)["commands"]
    assert commands == [first["command"], second["command"]]
    authority = service.authority
    assert all(
        verify(command, COMMAND_CONTEXT, authority.service_public_key(), authority.service_key_id)
        for command in commands
    )
    assert poll(http, token)["commands"] == commands
    result = {
        "type": "command_result",
        "command_id": first["command"]["command_id"],
        "status": "succeeded",
        "error": None,
        "effect": None,
        "finished_at": clock.stamp(),
    }
    expected = (200, {"type": "command_result_ack", "command_id": result["command_id"]})
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token) == expected
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token) == expected
    assert poll(http, token)["commands"] == [second["command"]]
    status, record = ask(f"{http}/api/nodes/main/commands/{result['command_id']}")
    assert status == 200 and record["status"] == "succeeded" and record["result"] == result
    events = [event for event in service.events.list().events if event.kind.startswith("command.")]
    assert [(event.kind, event.actor, event.subject) for event in events] == [
        ("command.issued", "root", "main"),
        ("command.issued", "root", "main"),
        ("command.result", "system", "main"),
    ]
    assert events[0].detail.to_data() == {"command_id": result["command_id"], "kind": "restart_unit"}
    assert events[-1].detail.to_data() == {
        "command_id": result["command_id"],
        "kind": "restart_unit",
        "status": "succeeded",
        "effect": None,
    }


def test_a_new_command_wakes_a_waiting_poll(http: str, clock: Clock) -> None:
    token = session(http, clock)
    document = poll(http, token)["desired"]
    have = {key: document[key] for key in ("fleet_epoch", "revision", "sha256")}
    response: dict[str, Any] = {}
    waiter = threading.Thread(target=lambda: response.update(poll(http, token, have=have, wait=30)))
    waiter.start()
    time.sleep(0.1)
    issued = issue(http, {"kind": "reconcile"})
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert response["desired"] is None and response["commands"] == [issued["command"]]


def test_only_the_kinds_this_session_accepted_are_delivered(http: str, clock: Clock) -> None:
    capable = session(http, clock, capabilities=["long_poll", "report"])
    restart = issue(http, {"kind": "restart_unit", "resource_id": "unit:netcore"})
    wanted = issue(http, {"kind": "report", "include_stats": False})
    assert poll(http, capable)["commands"] == [wanted["command"]]
    assert ask(f"{http}/api/nodes/main/commands/{restart['command']['command_id']}")[1]["status"] == "pending"


def test_a_revision_gate_waits_for_an_applied_report_and_wakes_the_poll(http: str, clock: Clock) -> None:
    token = session(http, clock)
    document = poll(http, token)["desired"]
    wanted = {key: document[key] for key in ("fleet_epoch", "revision", "sha256")}
    record = issue(http, {"kind": "reset_inbound", "inbound_tag": TAG, "wait_for": wanted})
    assert record["status"] == "waiting"
    assert poll(http, token, have=wanted)["commands"] == []
    response: dict[str, Any] = {}
    waiter = threading.Thread(target=lambda: response.update(poll(http, token, have=wanted, wait=30)))
    waiter.start()
    time.sleep(0.1)
    assert ask(f"{http}/agent/v1/report", "POST", report(document), token)[0] == 200
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert response["commands"] == [record["command"]]


def test_unknown_nodes_unmanaged_targets_and_other_nodes_results_are_refused(http: str, clock: Clock) -> None:
    assert ask(f"{http}/api/nodes/missing/commands", "POST", {"kind": "reconcile"})[0] == 404
    token = session(http, clock)
    assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "restart_unit", "resource_id": "netagent"})[0] == 400
    assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "reset_inbound", "inbound_tag": "missing"})[0] == 400
    record = issue(http, {"kind": "reconcile"})
    other = session(http, clock, node_id="other")
    result = {
        "type": "command_result",
        "command_id": record["command"]["command_id"],
        "status": "succeeded",
        "error": None,
        "effect": None,
        "finished_at": clock.stamp(),
    }
    assert ask(f"{http}/agent/v1/command-results", "POST", result, other)[0] == 400
    assert poll(http, token)["commands"] == [record["command"]]


def test_command_writes_need_admin_and_reads_need_read(http: str, service: ControlService) -> None:
    service.put_node_spec("main", spec())
    read = service.tokens.issue(TokenCreate(name="reader", scope="read"), issuer=ROOT)
    assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "reconcile"}, read.token)[0] == 403
    record = issue(http, {"kind": "reconcile"})
    assert ask(f"{http}/api/nodes/main/commands/{record['command']['command_id']}", token=read.token)[0] == 200
    people = service.tokens.issue(TokenCreate(name="accounts", scope="people"), issuer=ROOT)
    assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "reconcile"}, people.token)[0] == 403
    assert ask(f"{http}/api/nodes/main/commands/{record['command']['command_id']}", token=people.token)[0] == 403
    assert ask(f"{http}/api/nodes/main/command-targets", token=people.token)[0] == 403


def test_unanswered_commands_expire_and_old_finished_rows_are_pruned(
    http: str, service: ControlService, clock: Clock, tmp_path: Path
) -> None:
    token = session(http, clock)
    expired = issue(http, {"kind": "reconcile", "expires_in_seconds": 60})
    clock.now += 61
    assert poll(http, token)["commands"] == []
    path = f"{http}/api/nodes/main/commands/{expired['command']['command_id']}"
    assert ask(path)[1]["status"] == "expired"
    events = [event for event in service.events.list().events if event.kind.startswith("command.")]
    assert [event.kind for event in events] == ["command.issued", "command.result"]
    assert events[-1].detail.to_data()["status"] == "expired"
    service.commands.maintain()
    assert [event for event in service.events.list().events if event.kind.startswith("command.")] == events
    clock.now += 7 * 86400 + 1
    service.commands.maintain()
    assert ask(path)[0] == 404
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == 0


@pytest.mark.parametrize("pruned", [False, True], ids=["prune-in-result", "already-pruned"])
@pytest.mark.parametrize(
    ("kind", "status", "effect"),
    [
        ("restart_unit", "succeeded", None),
        ("reset_inbound", "succeeded", "core_restart"),
        ("restart_unit", "failed", None),
    ],
)
def test_review_2_1_late_execution_result_replaces_expiry_and_records_its_event(
    http: str, service: ControlService, clock: Clock, pruned: bool, kind: str, status: str, effect: str | None
) -> None:
    token = session(http, clock)
    fields = {"resource_id": "unit:netcore"} if kind == "restart_unit" else {"inbound_tag": TAG}
    issued = issue(http, {"kind": kind, "expires_in_seconds": 60, **fields})
    assert poll(http, token)["commands"] == [issued["command"]]
    clock.now = int(clock.now) + 60
    path = f"{http}/api/nodes/main/commands/{issued['command']['command_id']}"
    if pruned:
        assert ask(path)[1]["status"] == "expired"
    result = {
        "type": "command_result",
        "command_id": issued["command"]["command_id"],
        "status": status,
        "effect": effect,
        "error": {"code": "service_failed", "message": "The unit failed readiness.", "retryable": False}
        if status == "failed"
        else None,
        "finished_at": clock.stamp(),
    }
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token)[0] == 200
    stored = ask(path)[1]
    assert stored["status"] == status and stored["result"] == result
    events = [event for event in service.events.list().events if event.kind == "command.result"]
    assert [event.detail.to_data()["status"] for event in events] == ["expired", status]
    assert events[-1].detail.to_data()["effect"] == effect
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token)[0] == 200
    assert [event for event in service.events.list().events if event.kind == "command.result"] == events
    different = dict(result, status="expired", error=None, effect=None)
    assert ask(f"{http}/agent/v1/command-results", "POST", different, token)[0] == 200
    assert ask(path)[1]["result"] == result
    assert [event for event in service.events.list().events if event.kind == "command.result"] == events


def test_review_3_1_cli_removal_does_not_drop_resets_when_the_node_revision_changes(
    http: str, service: ControlService, clock: Clock, tmp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    user = service.put_user("person-a")
    session(http, clock)
    body = spec().model_dump(mode="json")
    body["resources"][0]["config"]["inbounds"].append(
        {"tag": "vless-xhttp", "protocol": "vless", "settings": {"clients": []}}
    )
    body["managed_inbounds"].append({"tag": "vless-xhttp", "protocol": "vless"})
    service.put_node_spec("main", NodeSpec.model_validate(body))
    binding = ManagedResourceBinding(
        logical_id="user:person-a", resource_kind="control_user", generation=1, remote_id="person-a", active=True
    )
    saved = ClusterConfig(
        panel=PanelConfig(url=http, api_token=ADMIN_TOKEN),
        topology_intent=single_exit_intent(users=["person-a", "other-person"]),
        managed_bindings={"user:person-a@1": binding},
    )
    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.get_user.return_value = User(
        username=user.username, id=user.user_id, short_uuid=user.sub_token, vless_uuid=user.uuid, status="ACTIVE"
    )
    reads: list[str] = []
    queued_during_retirement: list[int] = []

    class RacingClient(ControlClient):
        def node(self, node_id: str):
            current = super().node(node_id)
            reads.append(node_id)
            service.put_user("person-b")
            return current

    def apply(*args, **kwargs):
        context = SimpleNamespace(
            client=lambda: ControlClient(http, ADMIN_TOKEN), user_cut_off=kwargs.get("user_cut_off", {})
        )
        ControlUserDriver(context).retire(binding)
        queued_during_retirement.append(len(service._store.rows("SELECT command_id FROM commands")))

    def waited(client, commands):
        return [NodeCommandOutcome(node_id="main", status="done", commands=list(commands))] if commands else []

    set_json_mode(True)
    try:
        exit_code = 0
        with (
            patch("meridian.commands.client.load_topology_cluster", return_value=saved),
            patch("meridian.commands.client_v4.access_panel", return_value=panel),
            patch("meridian.commands.client_v4.apply_reviewed_intent", side_effect=apply),
            patch("meridian.node_commands.ControlClient", side_effect=lambda *args: RacingClient(*args)),
            patch("meridian.node_commands.wait_for_commands", side_effect=waited),
        ):
            try:
                run_remove("person-a", yes=True, cut_off=True)
            except typer.Exit as exc:
                exit_code = exc.exit_code
        result = json.loads(capsys.readouterr().out)
        assert exit_code == 0, json.dumps(result)
        assert queued_during_retirement == [2], "both resets must already be committed when retirement returns"
        assert reads == [], "atomic deletion must not read a revision then issue a separate reset"
        commands = result["data"]["cut_off"][0]["commands"]
        assert {record["command"]["inbound_tag"] for record in commands} == {TAG, "vless-xhttp"}
        assert all(record["wait_for"] is not None for record in commands)
    finally:
        set_json_mode(False)


def test_review_3_2_full_queue_keeps_the_user_and_a_resumable_cut_off_retirement(
    http: str, service: ControlService, clock: Clock, tmp_path: Path
) -> None:
    user = service.put_user("person-a")
    session(http, clock)
    for _ in range(64):
        service.issue_command("main", ReconcileCreate())
    before = service.node_status("main")
    events = service.events.list()
    binding = ManagedResourceBinding(
        logical_id="user:person-a", resource_kind="control_user", generation=1, remote_id="person-a", active=True
    )
    setattr(binding, "cut_off_on_retire", True)
    saved = ClusterConfig(managed_bindings={"user:person-a@1": binding})
    commands = []
    driver = ControlUserDriver(
        SimpleNamespace(client=lambda: ControlClient(http, ADMIN_TOKEN), user_cut_off={"person-a": commands})
    )
    error = None
    try:
        driver.retire(binding)
    except ControlServiceConflict as exc:
        error = exc
    assert service.get_user("person-a") == user, "a full reset queue must roll back deletion"
    assert isinstance(error, ControlServiceConflict)
    assert service.node_status("main") == before and service.events.list() == events
    path = tmp_path / "cluster.yml"
    saved.save(path)
    resumed = ClusterConfig.load(path).managed_bindings["user:person-a@1"]
    assert resumed.cut_off_on_retire, "a retry of meridian apply must still request the cut-off"
    clock.now += 301
    service.commands.maintain()
    driver.retire(resumed)
    assert service.get_user("person-a") is None and len(commands) == 1
    driver.retire(resumed)
    assert len(commands) == 1, "an already committed deletion must not queue another reset"


def test_reviewed_runtime_carries_atomic_cut_off_results_back_to_the_command(
    http: str, service: ControlService, clock: Clock, tmp_home: Path
) -> None:
    from meridian.commands._helpers import apply_reviewed_intent
    from meridian.compiler import compile_topology
    from meridian.reconciler.control_service_driver import ControlServiceDriverContext
    from meridian.reconciler.resources import ResourceExecutionResult
    from meridian.servers import ServerRegistry

    service.put_user("person-a")
    session(http, clock)
    intent = single_exit_intent(users=["other-person"])
    saved = ClusterConfig(panel=PanelConfig(url=http, api_token=ADMIN_TOKEN), topology_intent=intent)
    binding = ManagedResourceBinding(resource_kind="control_user", remote_id="person-a")
    commands = []

    def apply_runtime(runtime, intent, **kwargs):
        plan = compile_topology(intent)
        drivers = runtime._build_drivers(
            plan,
            saved,
            {},
            {},
            persist=lambda _state: None,
            control_context=ControlServiceDriverContext(ready=lambda _action: True, bootstrap=lambda _action: None),
        )
        drivers["control_user"].retire(binding)
        return ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1, results=[])

    with patch("meridian.setup.runtime.SetupRuntime.apply_intent", new=apply_runtime):
        apply_reviewed_intent(
            saved,
            ServerRegistry(tmp_home / "servers.json"),
            intent,
            failure="Removal failed",
            hint="Retry the apply",
            user_cut_off={"person-a": commands},
        )
    assert len(commands) == 1 and commands[0].wait_for is not None
    assert service.get_user("person-a") is None


@pytest.mark.parametrize("value", ["false", 1, []])
def test_persisted_cut_off_request_requires_a_boolean(value: Any, tmp_path: Path) -> None:
    binding = ManagedResourceBinding(logical_id="user:person-a", resource_kind="control_user", generation=1)
    binding.cut_off_on_retire = value
    saved = ClusterConfig(managed_bindings={"user:person-a@1": binding})
    with pytest.raises(ValueError, match="cut_off_on_retire must be a boolean"):
        saved.save(tmp_path / "cluster.yml")


def test_results_scrub_credentials_names_and_user_ids(http: str, service: ControlService, clock: Clock) -> None:
    token = session(http, clock)
    user = service.put_user("person-a")
    record = issue(http, {"kind": "restart_unit", "resource_id": "unit:netcore"})
    result = {
        "type": "command_result",
        "command_id": record["command"]["command_id"],
        "status": "failed",
        "error": {
            "code": "service_failed",
            "message": f"failed {user.username} {user.user_id} {user.uuid} {user.sub_token}",
            "retryable": False,
        },
        "effect": None,
        "finished_at": clock.stamp(),
    }
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token)[0] == 200
    stored = ask(f"{http}/api/nodes/main/commands/{result['command_id']}")[1]
    text = json.dumps(stored["result"]["error"])
    assert all(secret not in text for secret in (user.username, user.user_id, user.uuid, user.sub_token))
    assert len(stored["result"]["error"]["message"]) <= 300
    events = [event.to_data() for event in service.events.list().events if event.kind.startswith("command.")]
    text = json.dumps(events)
    assert all(secret not in text for secret in (user.username, user.user_id, user.uuid, user.sub_token))
    assert "message" not in text and "resource_id" not in text


def test_command_issue_records_the_request_actor(http: str, service: ControlService) -> None:
    service.put_node_spec("main", spec())
    admin = service.tokens.issue(TokenCreate(name="operator", scope="admin"), issuer=ROOT)
    assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "reconcile"}, admin.token)[0] == 200
    event = service.events.list().events[-1]
    assert (event.kind, event.actor, event.subject) == ("command.issued", "operator", "main")


@pytest.mark.parametrize("action", ["issue", "result", "late_result"])
def test_event_insert_failure_rolls_back_the_command_mutation(
    http: str, service: ControlService, clock: Clock, action: str
) -> None:
    token = session(http, clock)
    issued = issue(http, {"kind": "reconcile"})
    if action == "late_result":
        clock.now += 301
        service.commands.maintain()
    before = service.events.list()
    service._store.create(
        "CREATE TRIGGER refuse_command_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'event refused'); END;"
    )
    if action == "issue":
        assert ask(f"{http}/api/nodes/main/commands", "POST", {"kind": "reconcile"})[0] == 500
    else:
        result = {
            "type": "command_result",
            "command_id": issued["command"]["command_id"],
            "status": "succeeded",
            "error": None,
            "effect": None,
            "finished_at": clock.stamp(),
        }
        assert ask(f"{http}/agent/v1/command-results", "POST", result, token)[0] == 500
    assert poll(http, token)["commands"] == ([] if action == "late_result" else [issued["command"]])
    assert service.events.list() == before


def test_snapshots_do_not_replay_commands_or_restore_their_session_capabilities(
    http: str, service: ControlService, clock: Clock
) -> None:
    session(http, clock)
    issue(http, {"kind": "reconcile"})
    with sqlite3.connect(":memory:") as copy:
        service._store.db.backup(copy)
        strip_unsnapshotted(copy)
        assert copy.execute("SELECT count(*) FROM commands").fetchone()[0] == 0
        assert copy.execute("SELECT count(*) FROM agent_command_capabilities").fetchone()[0] == 0


def test_schema_fourteen_keeps_token_expiry_accounting_and_events_from_schema_thirteen(
    tmp_path: Path, clock: Clock
) -> None:
    path = tmp_path / "schema-thirteen.db"
    old = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    try:
        user = old.put_user("person-a")
        token = old.tokens.issue(TokenCreate(name="operator", scope="admin", expires_in_days=7), issuer=ROOT)
        old.record_stats(
            "main",
            StatsReport(
                counter_epoch="5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
                seq=1,
                collected_at=clock.stamp(),
                users=[{"user_id": user.user_id, "uplink_bytes": 10, "downlink_bytes": 20}],
            ),
            now=clock.stamp(),
        )
        accounting = old.traffic.node_accounting("main")
        events = old.events.list()
    finally:
        old.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE commands")
        db.execute("DROP TABLE agent_command_capabilities")
        db.execute("UPDATE meta SET value = '13' WHERE key = 'schema_version'")
    migrated = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    try:
        assert migrated.tokens.authenticate(token.token).scope == "admin"
        assert migrated.tokens.list_tokens().tokens[0].expires_at == token.expires_at
        assert migrated.traffic.node_accounting("main") == accounting
        assert migrated.events.list() == events
        restored_user = migrated.get_user("person-a")
        assert restored_user is not None and restored_user.lifetime_traffic_bytes == 30
        assert int(migrated._store.meta("schema_version")) == SCHEMA_VERSION
        migrated.put_node_spec("main", spec())
        issued = migrated.issue_command("main", ReconcileCreate())
        assert issued.status == "pending"
    finally:
        migrated.close()


@pytest.mark.parametrize("action", ["remove", "reissue"])
def test_cut_off_is_atomic_and_waits_for_the_revision_without_the_old_credentials(
    action: str, http: str, service: ControlService, clock: Clock
) -> None:
    user = service.put_user("person-a")
    token = session(http, clock)
    before = poll(http, token)["desired"]
    method = "DELETE" if action == "remove" else "POST"
    suffix = "" if action == "remove" else "/reissue"
    status, changed = ask(f"{http}/api/users/person-a{suffix}", method, {"cut_off": True})
    assert status == 200, changed
    commands = changed["commands"]
    assert len(commands) == 1 and commands[0]["status"] == "waiting"
    assert commands[0]["node_id"] == "main" and commands[0]["command"]["inbound_tag"] == TAG
    assert all(value not in json.dumps(commands) for value in (user.username, user.user_id, user.uuid, user.sub_token))
    after = poll(http, token, have={key: before[key] for key in ("fleet_epoch", "revision", "sha256")})
    assert after["commands"] == [] and after["desired"]["revision"] > before["revision"]
    wire = json.dumps(after["desired"]["resources"])
    assert user.uuid not in wire
    assert ask(f"{http}/agent/v1/report", "POST", report(after["desired"]), token)[0] == 200
    wanted = {key: after["desired"][key] for key in ("fleet_epoch", "revision", "sha256")}
    assert poll(http, token, have=wanted)["commands"] == [commands[0]["command"]]
    current = service.get_user(user.username)
    assert current is None if action == "remove" else current is not None and current.uuid != user.uuid


def test_a_full_command_queue_rolls_back_a_cut_off_mutation(http: str, service: ControlService, clock: Clock) -> None:
    user = service.put_user("person-a")
    session(http, clock)
    before = service.node_status("main")
    for _ in range(64):
        service.issue_command("main", ReconcileCreate())
    events = service.events.list()
    status, body = ask(f"{http}/api/users/person-a", "DELETE", {"cut_off": True})
    assert status == 409 and body["error"]["code"] == "command_queue_full"
    assert service.get_user(user.username) == user
    assert service.node_status("main").revision == before.revision
    assert service.events.list() == events


def test_cut_off_scope_lists_managed_inbounds_and_excludes_nodes_without_them(
    http: str, service: ControlService, clock: Clock
) -> None:
    service.put_user("person-a")
    session(http, clock)
    service.put_node_spec("another", spec())
    service.put_node_spec("no-users", NodeSpec(resources=[]))
    status, plan = ask(f"{http}/api/users/person-a/cut-off")
    assert status == 200
    assert plan == {
        "nodes": [{"node_id": "another", "inbound_tags": [TAG]}, {"node_id": "main", "inbound_tags": [TAG]}]
    }


def test_a_successful_reset_must_disclose_which_action_ended_sessions(http: str, clock: Clock) -> None:
    token = session(http, clock)
    record = issue(http, {"kind": "reset_inbound", "inbound_tag": TAG})
    result = {
        "type": "command_result",
        "command_id": record["command"]["command_id"],
        "status": "succeeded",
        "error": None,
        "effect": None,
        "finished_at": clock.stamp(),
    }
    assert ask(f"{http}/agent/v1/command-results", "POST", result, token)[0] == 400
    assert poll(http, token)["commands"] == [record["command"]]


def test_deleting_a_node_expires_commands_before_a_replacement_can_receive_them(
    http: str, service: ControlService, clock: Clock
) -> None:
    session(http, clock)
    record = issue(http, {"kind": "restart_unit", "resource_id": "unit:netcore"})
    service.delete_node("main")
    token = session(http, clock)
    assert poll(http, token)["commands"] == []
    assert ask(f"{http}/api/nodes/main/commands/{record['command']['command_id']}")[1]["status"] == "expired"
    results = [event for event in service.events.list().events if event.kind == "command.result"]
    assert len(results) == 1 and results[0].detail.to_data()["status"] == "expired"


@pytest.mark.parametrize("action", ["remove", "reissue"])
def test_people_tokens_cannot_end_sessions_during_credential_mutations(
    http: str, service: ControlService, clock: Clock, action: str
) -> None:
    user = service.put_user("person-a")
    session(http, clock)
    token = service.tokens.issue(TokenCreate(name="accounts", scope="people"), issuer=ROOT)
    method = "DELETE" if action == "remove" else "POST"
    suffix = "" if action == "remove" else "/reissue"
    status, body = ask(f"{http}/api/users/person-a{suffix}", method, {"cut_off": True}, token.token)
    assert status == 403 and body["error"]["code"] == "insufficient_scope"
    assert service.get_user(user.username) == user
    assert not any(event.kind.startswith("command.") for event in service.events.list().events)
