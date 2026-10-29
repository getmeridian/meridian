"""Meridian control-service drivers against a fake admin API and a mocked server."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import ResourcePlan
from meridian.control.models import (
    ClientEndpoint,
    EnrollmentGrant,
    FleetAuthority,
    NodeSpec,
    NodeStatus,
    ServiceSettings,
)
from meridian.control_artifacts import AgentBinary
from meridian.core.agent_protocol import AgentBootstrap
from meridian.core.agent_types import NodeStateRef, canonical_sha256
from meridian.core.topology import EgressOverrideIntent
from meridian.host_runtime import (
    AGENT_BINARY,
    AGENT_BOOTSTRAP,
    AGENT_CONFIG_DIR,
    AGENT_LIB_DIR,
    AGENT_STATE_DIR,
    CORE_UNIT,
)
from meridian.reconciler import meridian_drivers
from meridian.reconciler.meridian_drivers import MeridianDriverContext, build_meridian_drivers
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    build_resource_actions,
    observation_converges,
)
from meridian.reconciler.workloads import LocalRealityKeyFactory, WorkloadStateManager
from meridian.setup.single_server import single_server_intent
from tests.support.mock_connection import MockConnection
from tests.support.topology import GATEWAY_SERVERS, gateway_fleet_intent

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
CA_SHA256 = "c" * 64
CA_PEM = "-----BEGIN CERTIFICATE-----\n" + "A" * 64 + "\n-----END CERTIFICATE-----\n"
BINARY = AgentBinary(data=b"\x7fELF-agent", sha256=hashlib.sha256(b"\x7fELF-agent").hexdigest())


class FakeControl:
    """The admin API calls the drivers make, with scripted node reports."""

    def __init__(self) -> None:
        self.specs: dict[str, NodeSpec] = {}
        self.endpoints: dict[str, ClientEndpoint] = {}
        self.users: set[str] = set()
        self.tokens: list[str] = []
        # Each node() call pops the next scripted status; the last one repeats.
        self.reports: list[dict[str, Any]] = []
        self.current_settings = ServiceSettings(profile_title="Meridian")

    def __enter__(self) -> FakeControl:
        return self

    def __exit__(self, *args: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def node(self, node_id: str) -> NodeStatus | None:
        if node_id not in self.specs and not self.reports:
            return None
        report = self.reports.pop(0) if len(self.reports) > 1 else (self.reports[0] if self.reports else {})
        return self._status(node_id, **report)

    def put_node(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        self.specs[node_id] = spec
        return self._status(node_id)

    def grant_enrollment(self, node_id: str) -> EnrollmentGrant:
        self.tokens.append(f"enrollment-token-{len(self.tokens):032d}")
        return EnrollmentGrant(
            node_id=node_id,
            enrollment_token=self.tokens[-1],
            token_expires_at="2999-01-01T00:00:00Z",
            ca_sha256=CA_SHA256,
        )

    def fleet_authority(self) -> FleetAuthority:
        return FleetAuthority(ca_sha256=CA_SHA256, ca_certificate_pem=CA_PEM)

    def get_endpoint(self, endpoint_id: str) -> ClientEndpoint | None:
        return self.endpoints.get(endpoint_id)

    def put_endpoint(self, endpoint_id: str, endpoint: ClientEndpoint) -> ClientEndpoint:
        self.endpoints[endpoint_id] = endpoint
        return endpoint

    def settings(self) -> ServiceSettings:
        return self.current_settings

    def put_settings(self, settings: ServiceSettings) -> ServiceSettings:
        self.current_settings = settings
        return settings

    def _status(self, node_id: str, *, applied: bool = False, **fields: Any) -> NodeStatus:
        spec = self.specs.get(node_id)
        sha = "b" * 64
        return NodeStatus(
            node_id=node_id,
            fleet_epoch=1,
            revision=1,
            sha256=sha,
            spec_sha256=canonical_sha256(spec.model_dump(mode="json")) if spec else None,
            applied=NodeStateRef(fleet_epoch=1, revision=1, sha256=sha) if applied else None,
            last_seen_at=fields.pop("last_seen_at", None),
            agent_version=None,
            **fields,
        )


@pytest.fixture
def plan() -> ResourcePlan:
    return compile_topology(single_server_intent(None, server_ref="srv-a", address="198.51.100.10", control="meridian"))


def _setup(plan: ResourcePlan, conn: MockConnection | None = None, addresses: dict[str, str] | None = None):
    cluster = ClusterConfig()
    cluster.fleet_id = FLEET_ID
    cluster.panel.url = "https://198.51.100.10/secret"
    cluster.panel.sub_path = "pages"
    control = FakeControl()
    drivers = build_meridian_drivers(
        MeridianDriverContext(
            plan=plan,
            cluster=cluster,
            client=lambda: control,  # type: ignore[arg-type,return-value]
            workloads=WorkloadStateManager(cluster, persist=lambda _c: None, key_factory=LocalRealityKeyFactory()),
            connection_for=lambda _ref: conn or MockConnection(),
            server_addresses=addresses or {"srv-a": "198.51.100.10"},
            agent_binary=lambda _arch: BINARY,
            sleep=lambda _s: None,
        )
    )
    return drivers, control, cluster


def _action(plan: ResourcePlan, logical_id: str) -> ResourceAction:
    return next(action for action in build_resource_actions(plan, 1) if action.resource.logical_id == logical_id)


def test_workload_observation_never_allocates_reality_keys(plan: ResourcePlan) -> None:
    drivers, _control, cluster = _setup(plan)

    observation = drivers["node_workload"].observe(_action(plan, "workload:main"), None)

    assert not observation.exists
    assert cluster.workloads == []


def test_workload_converges_once_the_agent_applies_the_current_revision(plan: ResourcePlan) -> None:
    drivers, control, cluster = _setup(plan)
    action = _action(plan, "workload:main")
    control.reports = [{}, {"applied": True}]

    drivers["node_workload"].apply(action, None)

    assert cluster.workloads[0].reality_keys
    assert observation_converges(action, drivers["node_workload"].observe(action, None))


def test_workload_refuses_a_first_document_the_agent_failed(plan: ResourcePlan) -> None:
    drivers, control, _cluster = _setup(plan)
    control.reports = [{"failed": ["core-config"], "errors": ["xray rejected the config"]}]

    with pytest.raises(ResourceReconcileError, match="xray rejected the config"):
        drivers["node_workload"].apply(_action(plan, "workload:main"), None)


def test_reality_endpoint_publishes_the_workload_public_identity(plan: ResourcePlan) -> None:
    drivers, control, cluster = _setup(plan)
    control.reports = [{"applied": True}]
    drivers["node_workload"].apply(_action(plan, "workload:main"), None)
    action = _action(plan, "endpoint:main:main-reality:direct")

    drivers["client_endpoint"].apply(action, None)

    keys = next(iter(cluster.workloads[0].reality_keys.values()))
    endpoint = control.endpoints["main:main-reality:direct"]
    assert (endpoint.reality_public_key, endpoint.reality_short_id) == (keys.public_key, keys.short_id)
    assert endpoint.address == "198.51.100.10"
    assert observation_converges(action, drivers["client_endpoint"].observe(action, None))


def test_agent_bootstrap_is_the_contract_file_with_a_fresh_enrollment_token(plan: ResourcePlan) -> None:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    drivers, control, _cluster = _setup(plan, conn)
    control.reports = [{"last_seen_at": "2999-01-01T00:00:00Z"}]

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    bootstrap = AgentBootstrap.model_validate_json(conn.write_map[AGENT_BOOTSTRAP])
    assert bootstrap.enrollment_token == control.tokens[-1]
    assert bootstrap.ca_sha256 == CA_SHA256
    assert bootstrap.tls_pin_sha256 is None
    assert (bootstrap.service_url, bootstrap.fleet_id, bootstrap.node_id) == (
        "https://198.51.100.10/secret",
        FLEET_ID,
        "main",
    )
    assert conn.write_map[AGENT_BINARY] == BINARY.data
    write = next(kwargs for path, _data, kwargs in conn.writes if path == AGENT_BOOTSTRAP)
    assert write["mode"] == "600"
    assert not any("signing.pub" in call for call in conn.calls), "the CA hash comes from the admin API"


def _enrolled_bootstrap(**changes: object) -> str:
    body = {
        "protocol": "meridian.agent/v1",
        "service_url": "https://198.51.100.10/secret",
        "fleet_id": FLEET_ID,
        "node_id": "main",
        "enrollment_token": None,
        "token_expires_at": None,
        "ca_sha256": CA_SHA256,
        "tls_pin_sha256": None,
    }
    return json.dumps({**body, **changes})


@pytest.mark.parametrize(
    ("bootstrap", "binary", "active", "seen", "exists", "connected"),
    [
        (_enrolled_bootstrap(), BINARY.sha256, "active", "2999-01-01T00:00:00Z", True, True),
        (_enrolled_bootstrap(), BINARY.sha256, "active", "2000-01-01T00:00:00Z", True, False),
        (_enrolled_bootstrap(ca_sha256="d" * 64), BINARY.sha256, "active", "2999-01-01T00:00:00Z", False, False),
        (_enrolled_bootstrap(tls_pin_sha256="e" * 64), BINARY.sha256, "active", "2999-01-01T00:00:00Z", False, False),
        ("{not json", BINARY.sha256, "active", "2999-01-01T00:00:00Z", False, False),
        (_enrolled_bootstrap(), "f" * 64, "active", "2999-01-01T00:00:00Z", False, False),
        (_enrolled_bootstrap(), BINARY.sha256, "failed", "2999-01-01T00:00:00Z", False, False),
    ],
    ids=["enrolled", "not-seen", "other-ca", "pinned-tls", "malformed", "other-binary", "unit-down"],
)
def test_agent_observation_is_fail_closed(
    plan: ResourcePlan, bootstrap: str, binary: str, active: str, seen: str, exists: bool, connected: bool
) -> None:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    conn.when("sha256sum", stdout=f"{binary}  {AGENT_BINARY}\n")
    conn.when("systemctl is-active", stdout=f"{active}\n")
    conn.when(f"cat {AGENT_BOOTSTRAP}", stdout=bootstrap)
    drivers, control, _cluster = _setup(plan, conn)
    control.reports = [{"last_seen_at": seen}]
    action = _action(plan, "agent:main")

    observation = drivers["agent_runtime"].observe(action, None)

    assert observation.exists is exists
    assert observation_converges(action, observation) is connected


def test_settings_carry_the_public_base_of_pages_and_subscriptions(plan: ResourcePlan) -> None:
    drivers, control, _cluster = _setup(plan)
    action = _action(plan, "settings:service")

    assert not observation_converges(action, drivers["service_settings"].observe(action, None))
    drivers["service_settings"].apply(action, None)

    assert control.current_settings.public_url == "https://198.51.100.10/pages"
    assert observation_converges(action, drivers["service_settings"].observe(action, None))


def test_fingerprint_copies_reach_the_service_flagged(plan: ResourcePlan) -> None:
    drivers, control, _cluster = _setup(plan)
    control.reports = [{"applied": True}]
    drivers["node_workload"].apply(_action(plan, "workload:main"), None)

    drivers["client_endpoint"].apply(_action(plan, "endpoint:main:main-reality:direct:firefox"), None)

    assert control.endpoints["main:main-reality:direct:firefox"].fingerprint_copy is True


def _installed_agent(plan: ResourcePlan) -> tuple[dict[str, Any], FakeControl, MockConnection]:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    conn.when("sha256sum", stdout=f"{BINARY.sha256}  {AGENT_BINARY}\n")
    conn.when("systemctl is-active", stdout="active\n")
    conn.when(f"cat {AGENT_BOOTSTRAP}", stdout=_enrolled_bootstrap())
    drivers, control, _cluster = _setup(plan, conn)
    return drivers, control, conn


def test_an_installed_agent_back_from_a_service_outage_is_not_re_enrolled(plan: ResourcePlan) -> None:
    drivers, control, conn = _installed_agent(plan)
    control.reports = [{"last_seen_at": "2000-01-01T00:00:00Z"}] * 3 + [{"last_seen_at": "2999-01-01T00:00:00Z"}]

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert control.tokens == []
    assert AGENT_BINARY not in conn.write_map


def test_an_installed_agent_that_never_returns_is_enrolled_again(plan: ResourcePlan) -> None:
    drivers, control, conn = _installed_agent(plan)
    unseen = int(meridian_drivers.AGENT_RECONNECT_SECONDS / meridian_drivers.POLL_INTERVAL) + 1
    control.reports = [{"last_seen_at": "2000-01-01T00:00:00Z"}] * unseen + [{"last_seen_at": "2999-01-01T00:00:00Z"}]

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert len(control.tokens) == 1
    assert conn.write_map[AGENT_BINARY] == BINARY.data


def _warp_plan() -> ResourcePlan:
    intent = single_server_intent(None, server_ref="srv-a", address="198.51.100.10", control="meridian")
    return compile_topology(intent.model_copy(update={"exits": [intent.exits[0].model_copy(update={"warp": True})]}))


@pytest.mark.parametrize(("status", "converges"), [("Status update: Connected", True), ("Disconnected", False)])
def test_an_agent_with_warp_converges_only_while_warp_is_connected(status: str, converges: bool) -> None:
    plan = _warp_plan()
    drivers, control, conn = _installed_agent(plan)
    conn.when("warp-cli --accept-tos status", stdout=status)
    control.reports = [{"last_seen_at": "2999-01-01T00:00:00Z"}]
    action = _action(plan, "agent:main")

    assert observation_converges(action, drivers["agent_runtime"].observe(action, None)) is converges


def test_warp_repair_never_re_enrolls_a_healthy_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    plan = _warp_plan()
    drivers, control, conn = _installed_agent(plan)
    conn.when("warp-cli --accept-tos status", stdout="Disconnected")
    control.reports = [{"last_seen_at": "2999-01-01T00:00:00Z"}]

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert "warp-cli --accept-tos connect" in conn.calls
    assert control.tokens == []
    assert AGENT_BINARY not in conn.write_map


def _gateway_plan(*, warp: bool = False) -> ResourcePlan:
    intent = gateway_fleet_intent()
    if warp:
        main, de = intent.exits
        override = EgressOverrideIntent(id="ai", match="domain", values=["ai.example.org"], action="warp")
        exits = [main.model_copy(update={"warp": True}), de.model_copy(update={"egress_overrides": [override]})]
        intent = intent.model_copy(update={"exits": exits})
    return compile_topology(intent)


def _applied_fleet(plan: ResourcePlan) -> tuple[dict[str, Any], FakeControl, ClusterConfig]:
    """Apply every workload, exits before the gateway, as the plan orders them."""
    drivers, control, cluster = _setup(plan, addresses=GATEWAY_SERVERS)
    control.reports = [{"applied": True}]
    for workload in ("main", "de", "gw"):
        drivers["node_workload"].apply(_action(plan, f"workload:{workload}"), None)
    return drivers, control, cluster


def _core_config(spec: NodeSpec) -> dict[str, Any]:
    return next(item for item in spec.resources if item.id == "core-config").config  # type: ignore[union-attr]


def test_a_gateway_dials_each_exit_bridge_with_the_one_id_it_accepts() -> None:
    plan = _gateway_plan()
    drivers, control, cluster = _applied_fleet(plan)
    edges = {
        (outbound["settings"]["vnext"][0]["address"], outbound["settings"]["vnext"][0]["port"]): outbound
        for outbound in _core_config(control.specs["gw"])["outbounds"]
        if outbound["tag"].startswith("meridian-edge-")
    }

    for exit_ref, server_ref in (("main", "srv-a"), ("de", "srv-b")):
        spec = control.specs[exit_ref]
        bridge = next(
            item for item in _core_config(spec)["inbounds"] if item["tag"] == f"meridian-{exit_ref}-bridge-gw"
        )
        keys = next(item for item in cluster.workloads if item.id == exit_ref).reality_keys[server_ref]
        edge = edges[(GATEWAY_SERVERS[server_ref], bridge["port"])]
        (client,) = bridge["settings"]["clients"]
        assert client == {"id": edge["settings"]["vnext"][0]["users"][0]["id"], "flow": "xtls-rprx-vision"}
        reality = edge["streamSettings"]["realitySettings"]
        assert (reality["publicKey"], reality["shortId"]) == (keys.public_key, keys.short_id)
        # Users never reach a bridge: the service fills only the managed inbounds.
        assert bridge["tag"] not in {inbound.tag for inbound in spec.managed_inbounds}
    assert [inbound.tag for inbound in control.specs["gw"].managed_inbounds] == ["meridian-gw-reality"]
    action = _action(plan, "workload:gw")
    assert observation_converges(action, drivers["node_workload"].observe(action, None))


def test_a_gateway_workload_is_unobserved_until_its_exits_hold_keys() -> None:
    plan = _gateway_plan()
    drivers, control, cluster = _setup(plan, addresses=GATEWAY_SERVERS)
    control.reports = [{"applied": True}]
    drivers["node_workload"].apply(_action(plan, "workload:main"), None)
    action = _action(plan, "workload:gw")

    assert not drivers["node_workload"].observe(action, None).exists
    with pytest.raises(ResourceReconcileError, match="has no Reality keys yet"):
        drivers["node_workload"].apply(action, None)


def _self_signed(directory: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vpn.example.com")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "fullchain.pem", directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return str(cert_path), str(key_path)


def _composed(spec: NodeSpec, certificate: tuple[str, str]) -> dict[str, Any]:
    """The config as an agent writes it: one user per managed inbound, the certificate on this machine."""
    config = copy.deepcopy(_core_config(spec))
    managed = {inbound.tag: inbound for inbound in spec.managed_inbounds}
    for inbound in config["inbounds"]:
        item = managed.get(inbound["tag"])
        if item is not None and item.protocol == "hysteria2":
            inbound["settings"]["clients"] = [{"auth": str(uuid.uuid4()), "email": "user-1"}]
        elif item is not None:
            client = {"id": str(uuid.uuid4()), "email": "user-1"}
            inbound["settings"]["clients"] = [{**client, "flow": item.flow} if item.flow else client]
        for entry in inbound.get("streamSettings", {}).get("tlsSettings", {}).get("certificates", []):
            entry["certificateFile"], entry["keyFile"] = certificate
    return config


@pytest.mark.parametrize("warp", [False, True], ids=["direct", "warp"])
def test_rendered_exit_and_gateway_configs_pass_xray_test(xray_binary: Path, tmp_path: Path, warp: bool) -> None:
    _drivers, control, _cluster = _applied_fleet(_gateway_plan(warp=warp))
    certificate = _self_signed(tmp_path)

    for node_id, spec in control.specs.items():
        config_file = tmp_path / f"{node_id}.json"
        config_file.write_text(json.dumps(_composed(spec, certificate)), encoding="utf-8")
        result = subprocess.run(
            [str(xray_binary), "run", "-test", "-c", str(config_file)],
            env={**os.environ, "XRAY_LOCATION_ASSET": str(xray_binary.parent)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"{node_id}: {result.stdout}{result.stderr}"


def _agent_binding(server_ref: str) -> ManagedResourceBinding:
    return ManagedResourceBinding(
        logical_id="agent:old", resource_kind="agent_runtime", remote_id="old", server_ref=server_ref
    )


@pytest.mark.parametrize(("rc", "retires"), [(0, True), (1, False)], ids=["stopped", "still-running"])
def test_retiring_an_agent_stops_and_deletes_its_core_too(plan: ResourcePlan, rc: int, retires: bool) -> None:
    conn = MockConnection().when("systemctl daemon-reload", rc=rc)
    drivers, _control, _cluster = _setup(plan, conn)

    if retires:
        drivers["agent_runtime"].retire(_agent_binding("srv-gone"))  # type: ignore[union-attr]
    else:
        with pytest.raises(ResourceReconcileError, match="still running"):
            drivers["agent_runtime"].retire(_agent_binding("srv-gone"))  # type: ignore[union-attr]

    (command,) = conn.calls
    assert all(part in command for part in (CORE_UNIT, AGENT_LIB_DIR, AGENT_CONFIG_DIR, AGENT_STATE_DIR))


def test_an_agent_the_plan_still_runs_on_that_server_is_only_forgotten(plan: ResourcePlan) -> None:
    conn = MockConnection()
    drivers, _control, _cluster = _setup(plan, conn)

    drivers["agent_runtime"].retire(_agent_binding("srv-a"))  # type: ignore[union-attr]

    assert conn.calls == []
