"""Contract tests for the `meridian.agent/v1` control-service ↔ node-agent protocol."""

from __future__ import annotations

import base64
import copy
import hashlib
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from meridian.core.agent_desired import AgentResourceSpec, DesiredNodeState, ResourceKind
from meridian.core.agent_http import AGENT_HTTP_SURFACE
from meridian.core.agent_protocol import AgentCommandSpec, AgentErrorCode, AgentMessageSpec, StatsAck, StatsReport
from meridian.core.agent_types import canonical_sha256
from meridian.core.schema import schema_names
from meridian.core.schema_agent import AGENT_SCHEMAS, agent_schema
from scripts.export_contracts import build_contract_files

FLEET = "0f8fad5b-d9cb-469f-a165-70867728950e"
SIGNATURE = {"key_id": "a" * 64, "signature": "A" * 86 + "=="}
NOW = "2026-10-28T12:00:00Z"
UNIT_TEXT = "[Service]\nExecStart=/usr/local/bin/xray run -c /etc/xray/config.json\n"
NGINX_TEXT = "server { listen 127.0.0.1:8443 ssl; }\n"
FILE_BYTES = b"geo-block list\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resources() -> list[dict[str, Any]]:
    """One resource of every kind, dependency-ordered."""
    return [
        {
            "kind": "binary_pin",
            "id": "bin.xray",
            "depends_on": [],
            "name": "xray",
            "version": "26.7.11",
            "sources": ["https://github.com/XTLS/Xray-core/releases/download/v26.7.11/Xray-linux-64.zip", "service"],
            "size": 12_000_000,
            "sha256": "b" * 64,
            "archive": "zip",
            "member": "xray",
            "install_path": "/usr/local/bin/xray",
            "mode": "0755",
        },
        {
            "kind": "file",
            "id": "file.geo",
            "depends_on": [],
            "path": "/etc/xray/geo-block.txt",
            "mode": "0644",
            "owner": "root",
            "group": "root",
            "size": len(FILE_BYTES),
            "sha256": _sha(FILE_BYTES),
            "content_b64": base64.b64encode(FILE_BYTES).decode(),
            "sensitive": False,
        },
        {
            "kind": "xray_config",
            "id": "xray.config",
            "depends_on": ["bin.xray", "file.geo"],
            "path": "/etc/xray/config.json",
            "api_address": "127.0.0.1:10085",
            "config": {
                "inbounds": [
                    {"tag": "vless-reality", "protocol": "vless", "settings": {"clients": [], "decryption": "none"}},
                    {"tag": "hysteria2", "protocol": "hysteria", "settings": {"clients": [], "version": 2}},
                ]
            },
        },
        {
            "kind": "systemd_unit",
            "id": "unit.xray",
            "depends_on": ["xray.config"],
            "name": "xray.service",
            "content": UNIT_TEXT,
            "enabled": True,
            "state": "started",
            "restart_on": ["xray.config"],
        },
        {
            "kind": "xray_users",
            "id": "users.vless",
            "depends_on": ["unit.xray"],
            "inbound_tag": "vless-reality",
            "users": [{"user_id": "u-1", "uuid": "9b2f0c3e-6f4a-4c43-9d55-0d7c1d3f6a10", "flow": "xtls-rprx-vision"}],
        },
        {
            "kind": "hysteria2_users",
            "id": "users.hy2",
            "depends_on": ["unit.xray"],
            "inbound_tag": "hysteria2",
            "users": [{"user_id": "u-1", "auth": "s3cr3t-auth-value-0123"}],
        },
        {
            "kind": "nginx_artifact",
            "id": "nginx.stream",
            "depends_on": [],
            "files": [
                {"path": "/etc/nginx/conf.d/meridian.conf", "content": NGINX_TEXT, "sha256": _sha(NGINX_TEXT.encode())}
            ],
        },
        {
            "kind": "realm_forwarder",
            "id": "realm.exit-1",
            "depends_on": [],
            "listen_address": "0.0.0.0",
            "listen_port": 8444,
            "remote_host": "198.51.100.20",
            "remote_port": 443,
        },
        {
            "kind": "firewall_rule",
            "id": "fw.443",
            "depends_on": [],
            "port": 443,
            "protocol": "tcp",
            "allow_from": ["198.51.100.30/32"],
        },
        {
            "kind": "agent_upgrade",
            "id": "agent.upgrade",
            "depends_on": [],
            "version": "0.2.0",
            "sources": ["service"],
            "size": 3_000_000,
            "sha256": "c" * 64,
            "health_timeout_seconds": 120,
        },
    ]


def _desired(resources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = _resources() if resources is None else resources
    canonical = [AgentResourceSpec.model_validate(item).model_dump(mode="json") for item in items]
    return {
        "protocol": "meridian.agent/v1",
        "fleet_id": FLEET,
        "node_id": "exit-1",
        "fleet_epoch": 2,
        "revision": 41,
        "sha256": canonical_sha256(canonical),
        "issued_at": NOW,
        "resources": items,
        "signature": SIGNATURE,
    }


def _command(kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "command_id": "4c1d8f0e-2a7b-4d7e-9f61-3b5a0c9e8d21",
        "fleet_epoch": 2,
        "issued_at": NOW,
        "expires_at": "2026-10-28T12:10:00Z",
        "signature": SIGNATURE,
        **extra,
    }


COMMANDS = [
    _command("reconcile"),
    _command("report", include_stats=True),
    _command("reset_inbound", inbound_tag="vless-reality"),
    _command("restart_unit", resource_id="unit.xray"),
    _command("renew_credential"),
]


def test_poll_result_with_every_resource_and_command_round_trips() -> None:
    payload = {
        "type": "poll_result",
        "fleet_epoch": 2,
        "desired": _desired(),
        "commands": COMMANDS,
        "poll_after_seconds": 0,
    }

    message = AgentMessageSpec.model_validate(payload)
    wire = message.model_dump_json(by_alias=True)

    assert AgentMessageSpec.model_validate_json(wire) == message
    desired = message.root.desired  # type: ignore[union-attr]
    assert desired is not None
    assert [item.kind for item in desired.resources] == [item["kind"] for item in _resources()]
    assert {item.kind for item in desired.resources} == set(get_args(ResourceKind))


def test_desired_state_hash_covers_resources_only() -> None:
    base = DesiredNodeState.model_validate(_desired())
    bumped = DesiredNodeState.model_validate({**_desired(), "revision": 42})
    resources = _resources()
    resources[-1]["health_timeout_seconds"] = 300
    edited = DesiredNodeState.model_validate(_desired(resources))

    assert bumped.sha256 == base.sha256
    assert edited.sha256 != base.sha256


def _drop(field: str) -> Any:
    return lambda doc: doc.pop(field)


def _set(path: tuple[Any, ...], value: Any) -> Any:
    def mutate(doc: dict[str, Any]) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _append(resource: dict[str, Any]) -> Any:
    return lambda doc: doc["resources"].append(resource)


@pytest.mark.parametrize(
    "mutate",
    [
        _drop("fleet_epoch"),
        _drop("revision"),
        _drop("sha256"),
        _drop("signature"),
        _set(("fleet_epoch",), 0),
        _set(("revision",), "41"),
        _set(("sha256",), "0" * 64),
        _set(("fleet_id",), "not-a-uuid"),
        _append({"kind": "docker_container", "id": "c.1", "depends_on": []}),
        _set(("resources", 1, "id"), "bin.xray"),
        _set(("resources", 0, "depends_on"), ["file.geo"]),
        _set(("resources", 3, "restart_on"), ["file.geo"]),
        _set(("resources", 1, "content_b64"), base64.b64encode(b"tampered").decode()),
        _set(("resources", 1, "path"), "/etc/xray/../shadow"),
        _set(("resources", 1, "path"), "/etc/xray/.env"),
        _set(("resources", 1, "path"), "/usr/local/bin/xray"),
        _set(("resources", 2, "config", "inbounds", 0, "settings", "clients"), [{"id": "x"}]),
        _set(("resources", 4, "inbound_tag"), "undeclared"),
        _set(("resources", 2, "config", "policy"), {"ratio": 0.5}),
        _set(("resources", 0, "member"), None),
        _set(("resources", 6, "files", 0, "path"), "/etc/ssh/sshd_config"),
        _set(("resources", 8, "allow_from"), ["198.51.100.30/24"]),
    ],
    ids=[
        "missing-epoch",
        "missing-revision",
        "missing-hash",
        "missing-signature",
        "zero-epoch",
        "revision-as-string",
        "hash-mismatch",
        "bad-fleet-id",
        "unknown-resource-kind",
        "duplicate-resource-id",
        "forward-dependency",
        "restart-on-outside-depends-on",
        "inline-content-mismatch",
        "path-traversal",
        "hidden-file",
        "two-resources-one-path",
        "managed-inbound-with-clients",
        "users-for-undeclared-inbound",
        "float-in-xray-config",
        "archive-without-member",
        "nginx-file-outside-nginx",
        "cidr-with-host-bits",
    ],
)
def test_desired_state_rejects_invalid_documents(mutate: Any) -> None:
    payload = _desired()
    original = copy.deepcopy(payload)
    mutate(payload)
    if (
        payload["resources"] != original["resources"]
        and "sha256" in payload
        and payload["sha256"] == original["sha256"]
    ):
        # Re-hash so the case fails on its own invariant, not on the stale hash.
        payload["sha256"] = canonical_sha256(payload["resources"])

    with pytest.raises(ValidationError):
        DesiredNodeState.model_validate(payload)


@pytest.mark.parametrize("command", COMMANDS, ids=[item["kind"] for item in COMMANDS])
@pytest.mark.parametrize("change", ["missing", "not-uuid"])
def test_commands_require_a_uuid_command_id(command: dict[str, Any], change: str) -> None:
    assert AgentCommandSpec.model_validate(command).root.command_id == command["command_id"]
    broken = dict(command)
    if change == "missing":
        broken.pop("command_id")
    else:
        broken["command_id"] = "retry-1"

    with pytest.raises(ValidationError):
        AgentCommandSpec.model_validate(broken)


def _property_names(schema: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= _property_names(item)
    return names


def _has_ip_format(schema: Any) -> bool:
    if isinstance(schema, dict):
        if schema.get("format") in {"ipv4", "ipv6", "ipvanyaddress"}:
            return True
        return any(_has_ip_format(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_has_ip_format(item) for item in schema)
    return False


CLIENT_ADDRESS_WORDS = (
    "client_ip",
    "client_addr",
    "remote_addr",
    "remote_ip",
    "peer",
    "source_ip",
    "src_ip",
    "user_ip",
)
CLIENT_ADDRESS_NAMES = {"ip", "ips", "last_ip", "real_ip", "x_forwarded_for", "forwarded_for", "origin_ip"}
AGENT_SENT = [
    "agent-enroll-request",
    "agent-hello-request",
    "agent-renew-request",
    "agent-poll-request",
    "agent-observed-state-report",
    "agent-stats-report",
    "agent-command-result",
]


def test_no_field_anywhere_names_a_client_address() -> None:
    names = set().union(*(_property_names(agent_schema(name)) for name in AGENT_SCHEMAS))

    offenders = {name for name in names if name in CLIENT_ADDRESS_NAMES or any(w in name for w in CLIENT_ADDRESS_WORDS)}
    assert offenders == set()


@pytest.mark.parametrize("name", AGENT_SENT)
def test_agent_sent_messages_carry_no_address_shaped_field(name: str) -> None:
    schema = agent_schema(name)
    address_like = {
        prop
        for prop in _property_names(schema)
        if prop.endswith(("_ip", "_addr", "_address", "_cidr", "host")) or prop in {"address", "allow_from"}
    }

    assert not _has_ip_format(schema)
    assert address_like == set()


def test_stats_ack_counts_each_report_once() -> None:
    """Simulates the service ledger the protocol prescribes: count growth past the last acked seq only."""
    epoch = "7d444840-9dc0-41a1-b2c5-2d5f6b4bd8f2"
    ledger: dict[str, Any] = {"epoch": None, "seq": 0, "totals": {}}
    counted: dict[str, int] = {}

    def receive(report: StatsReport) -> StatsAck:
        if report.counter_epoch != ledger["epoch"]:
            ledger.update(epoch=report.counter_epoch, seq=0, totals={})
        if report.seq > ledger["seq"]:
            for item in report.users:
                total = item.uplink_bytes + item.downlink_bytes
                counted[item.user_id] = counted.get(item.user_id, 0) + total - ledger["totals"].get(item.user_id, 0)
                ledger["totals"][item.user_id] = total
            ledger["seq"] = report.seq
        return StatsAck(counter_epoch=report.counter_epoch, acked_seq=ledger["seq"])

    def report(seq: int, total: int, counter_epoch: str = epoch) -> StatsReport:
        users = [{"user_id": "u-1", "uplink_bytes": total, "downlink_bytes": 0}]
        return StatsReport.model_validate(
            {"type": "stats", "counter_epoch": counter_epoch, "seq": seq, "collected_at": NOW, "users": users}
        )

    assert receive(report(1, 100)).acked_seq == 1
    assert receive(report(1, 100)).acked_seq == 1  # ack lost, resent: not counted twice
    assert receive(report(2, 250)).acked_seq == 2
    assert receive(report(1, 100)).acked_seq == 2  # late duplicate of an older report
    assert receive(report(1, 40, "2b9f4c1e-8a3d-4e5f-9b6a-7c8d9e0f1a2b")).acked_seq == 1  # store lost: from zero
    assert counted == {"u-1": 290}


@pytest.mark.parametrize(
    "users",
    [
        [{"user_id": "u-1", "uplink_bytes": 1, "downlink_bytes": 1}] * 2,
        [{"user_id": "u-1", "uplink_bytes": -1, "downlink_bytes": 0}],
        [{"user_id": "u-1", "uplink_bytes": 1.5, "downlink_bytes": 0}],
    ],
    ids=["duplicate-user", "negative-counter", "float-counter"],
)
def test_stats_report_rejects_ambiguous_counters(users: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        StatsReport.model_validate(
            {"type": "stats", "counter_epoch": FLEET, "seq": 1, "collected_at": NOW, "users": users}
        )


def test_agent_contract_is_exported_apart_from_the_public_catalog() -> None:
    files = {path.as_posix() for path in build_contract_files()}

    assert {f"agent/schemas/{name}.schema.json" for name in AGENT_SCHEMAS} | {"agent/http.json"} <= files
    assert not any(path.startswith("schemas/agent-") for path in files)
    assert not set(AGENT_SCHEMAS) & set(schema_names())


def test_http_surface_references_published_schemas_and_maps_every_error_once() -> None:
    names = set(AGENT_SCHEMAS)
    endpoints = AGENT_HTTP_SURFACE.endpoints

    assert AGENT_HTTP_SURFACE.error_schema in names
    for endpoint in endpoints:
        assert endpoint.request_schema is None or endpoint.request_schema in names
        assert endpoint.response_schema is None or endpoint.response_schema in names
    assert len({item.name for item in endpoints}) == len(endpoints)
    assert [item.name for item in endpoints if item.long_poll] == ["poll"]
    assert sorted(item.code for item in AGENT_HTTP_SURFACE.errors) == sorted(get_args(AgentErrorCode))
