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
from meridian.control.client import ControlServiceError, ControlServiceUncertain
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
from meridian.reconciler import agent_upgrade, meridian_drivers
from meridian.reconciler.meridian_drivers import MeridianDriverContext, build_meridian_drivers
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    UnknownResourceOutcome,
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
            agent_version=fields.pop("agent_version", None),
            **fields,
        )


@pytest.fixture
def plan() -> ResourcePlan:
    return compile_topology(single_server_intent(None, server_ref="srv-a", address="198.51.100.10", control="meridian"))


def _setup(
    plan: ResourcePlan,
    conn: MockConnection | None = None,
    addresses: dict[str, str] | None = None,
    control: FakeControl | None = None,
):
    cluster = ClusterConfig()
    cluster.fleet_id = FLEET_ID
    cluster.panel.url = "https://198.51.100.10/secret"
    cluster.panel.sub_path = "pages"
    control = control or FakeControl()
    drivers = build_meridian_drivers(
        MeridianDriverContext(
            plan=plan,
            cluster=cluster,
            client=lambda: control,  # type: ignore[arg-type,return-value]
            workloads=WorkloadStateManager(cluster, persist=lambda _c: None, key_factory=LocalRealityKeyFactory()),
            connection_for=lambda _ref: conn or MockConnection().when("uname -m", stdout="x86_64\n"),
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


def test_an_arm_exit_gets_the_arm_xray_archive(plan: ResourcePlan) -> None:
    conn = MockConnection().when("uname -m", stdout="aarch64\n")
    drivers, control, _cluster = _setup(plan, conn)
    control.reports = [{"applied": True}]

    drivers["node_workload"].apply(_action(plan, "workload:main"), None)

    (spec,) = control.specs.values()
    pins = [resource for resource in spec.resources if resource.kind == "binary_pin"]
    assert pins and all(pin.sources[0].endswith("/Xray-linux-arm64-v8a.zip") for pin in pins)
    assert sum("uname -m" in call for call in conn.calls) == 1, "read once per apply"


def test_workload_refuses_a_first_document_the_agent_failed(plan: ResourcePlan) -> None:
    drivers, control, _cluster = _setup(plan)
    control.reports = [{"failed": ["core-config"], "errors": ["xray rejected the config"]}]

    with pytest.raises(ResourceReconcileError, match="xray rejected the config"):
        drivers["node_workload"].apply(_action(plan, "workload:main"), None)


class _CommittedLostReply(FakeControl):
    """The service stores every write, then the reply never arrives."""

    def put_node(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        super().put_node(node_id, spec)
        raise ControlServiceUncertain("Could not reach the control service: ReadTimeout", retryable=True)

    def put_user(self, username: str) -> None:
        self.users.add(username)
        raise ControlServiceUncertain("Could not reach the control service: ReadTimeout", retryable=True)


@pytest.mark.parametrize("logical_id", ["workload:main", "account:default"], ids=["node-spec", "user"])
def test_a_write_whose_reply_was_lost_is_an_unknown_outcome(plan: ResourcePlan, logical_id: str) -> None:
    """A definite failure would let another plan replace this one before anyone looked."""
    drivers, _control, _cluster = _setup(plan, control=_CommittedLostReply())
    action = _action(plan, logical_id)

    with pytest.raises(UnknownResourceOutcome, match="may have taken effect"):
        drivers[action.resource.payload.kind].apply(action, None)


def test_an_agent_install_that_lost_the_server_is_an_unknown_outcome(plan: ResourcePlan) -> None:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    conn.when("systemctl is-active", stdout="inactive\n", rc=3)
    conn.when("systemctl restart", rc=255)
    drivers, _control, _cluster = _setup(plan, conn)

    with pytest.raises(UnknownResourceOutcome, match="Lost the server"):
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)


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


@pytest.mark.parametrize(
    ("pattern", "rc", "stderr", "retryable"),
    [
        ("systemctl is-active", 255, "", True),
        (f"cat {AGENT_BOOTSTRAP}", 124, "", True),
        (f"cat {AGENT_BOOTSTRAP}", 1, "cat: bootstrap.json: Permission denied", False),
        ("MainPID", 255, "", True),
    ],
    ids=["unit-lost", "bootstrap-timed-out", "bootstrap-denied", "image-lost"],
)
def test_an_unreadable_agent_is_unavailable_evidence_never_re_enrolled(
    plan: ResourcePlan, pattern: str, rc: int, stderr: str, retryable: bool
) -> None:
    host = AgentHost()
    host.binary = host.running = BINARY.sha256
    host.failing[pattern] = subprocess.CompletedProcess([], rc, "", stderr)
    drivers, control = _upgrade_setup(plan, host, [{"last_seen_at": _SEEN, "agent_version": NEW_VERSION}])

    with pytest.raises(ResourceReconcileError, match="evidence is unavailable") as raised:
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert raised.value.retryable is retryable
    assert control.tokens == []
    assert AGENT_BOOTSTRAP not in host.write_map


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


# --- agent upgrades: a new build keeps the node's identity -------------------------------

OLD_SHA = "0" * 64
OLD_VERSION, NEW_VERSION = "0.1.0+0123456789ab", "0.2.0+ba9876543210"


class AgentHost(MockConnection):
    """A node whose agent binary, staged copy, previous copy, and running image follow the commands."""

    def __init__(
        self, *, check_rc: int = 0, enrolled: bool = True, active: bool = True, swap_rc: int = 0, rollback_rc: int = 0
    ) -> None:
        super().__init__()
        self.binary, self.staged, self.previous, self.running = OLD_SHA, "", "", OLD_SHA
        self.check_rc, self.enrolled = check_rc, enrolled
        # A failing swap renamed the new build in, but its restart failed (or the reply was lost).
        self.swap_rc, self.rollback_rc = swap_rc, rollback_rc
        # Commands (by substring) whose inspection fails, ahead of everything the node answers.
        self.failing: dict[str, subprocess.CompletedProcess[str]] = {}
        self.when("uname -m", stdout="x86_64\n")
        self.when("systemctl is-active", stdout="active\n" if active else "failed\n", rc=0 if active else 3)
        self.when(f"cat {AGENT_BOOTSTRAP}", stdout=_enrolled_bootstrap())

    def put_bytes(self, path: str, data: bytes, timeout: int = 30, **kwargs: object) -> Any:
        if path == agent_upgrade.STAGED_BINARY:
            self.staged = hashlib.sha256(data).hexdigest()
        if path == AGENT_BINARY:
            self.binary = hashlib.sha256(data).hexdigest()
        return super().put_bytes(path, data, timeout=timeout, **kwargs)

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> Any:
        result = super().run(command, timeout=timeout, **kwargs)
        failed = next((reply for pattern, reply in self.failing.items() if pattern in command), None)
        if failed is not None:
            return failed
        staged, previous = agent_upgrade.STAGED_BINARY, agent_upgrade.PREVIOUS_BINARY
        if f"mv -f {staged} {AGENT_BINARY}" in command:
            # The previous binary is whatever the command copies; the staged build is renamed in and restarted.
            kept = self.running if "cp /proc/$pid/exe" in command else self.binary
            self.previous, self.binary, self.staged = kept, self.staged, ""
            self.running = self.binary if not self.swap_rc else ""
            return subprocess.CompletedProcess([], self.swap_rc, "", "restart failed" if self.swap_rc else "")
        if command.startswith(f"mv -f {previous} {AGENT_BINARY}"):
            if self.rollback_rc:
                return subprocess.CompletedProcess([], self.rollback_rc, "", "")
            self.binary, self.previous = self.previous, ""
            self.running = self.binary
            return result
        if "MainPID" in command:
            return subprocess.CompletedProcess([], 0, f"{self.running}  /proc/4242/exe\n", "")
        if command.startswith(f"sha256sum {staged}"):
            return subprocess.CompletedProcess([], 0, f"{self.staged}  {staged}\n", "")
        if command.startswith(f"{staged} check"):
            report = {"version": NEW_VERSION, "node_id": "main", "enrolled": self.enrolled, "held_verifies": True}
            if self.check_rc:
                return subprocess.CompletedProcess([], 1, "", "netagent: state.json is malformed")
            return subprocess.CompletedProcess([], 0, json.dumps(report) + "\n", "")
        if command == f"rm -f {previous}":
            self.previous = ""
        elif command == f"rm -f {staged}":
            self.staged = ""
        return result


def _upgrade_setup(plan: ResourcePlan, host: AgentHost, reports: list[dict[str, Any]]) -> tuple[Any, FakeControl]:
    drivers, control, _cluster = _setup(plan, host)
    control.reports = reports
    return drivers, control


_SEEN = "2999-01-01T00:00:00Z"
_LATER = "2999-01-01T00:00:09Z"


def test_a_new_agent_build_is_swapped_in_keeping_the_nodes_identity(plan: ResourcePlan) -> None:
    host = AgentHost()
    old = {"last_seen_at": _SEEN, "agent_version": OLD_VERSION}
    new = {"last_seen_at": _LATER, "agent_version": NEW_VERSION}
    drivers, control = _upgrade_setup(plan, host, [old, old, old, new])

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert (host.binary, host.running, host.previous, host.staged) == (BINARY.sha256, BINARY.sha256, "", "")
    assert control.tokens == [], "no enrollment token: the node key and certificate stay"
    assert AGENT_BOOTSTRAP not in host.write_map
    calls = host.calls
    check = next(i for i, call in enumerate(calls) if " check --bootstrap" in call)
    swap = next(i for i, call in enumerate(calls) if f"mv -f {agent_upgrade.STAGED_BINARY}" in call)
    assert check < swap, "the staged build checks the node's files before it replaces anything"
    action = _action(plan, "agent:main")
    assert observation_converges(action, drivers["agent_runtime"].observe(action, None))


def test_an_upgraded_agent_that_never_reports_is_rolled_back(
    plan: ResourcePlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_upgrade, "UPGRADE_REPORT_TIMEOUT", 0.0)
    host = AgentHost()
    old = {"last_seen_at": _SEEN, "agent_version": OLD_VERSION}
    drivers, control = _upgrade_setup(plan, host, [old])

    with pytest.raises(ResourceReconcileError, match="the previous agent is back"):
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert (host.binary, host.running, host.previous) == (OLD_SHA, OLD_SHA, "")
    assert control.tokens == []
    assert AGENT_BOOTSTRAP not in host.write_map


@pytest.mark.parametrize(
    ("check_rc", "enrolled", "message"),
    [(1, True, "state.json is malformed"), (0, False, "cannot take over")],
    ids=["unreadable-state", "not-enrolled"],
)
def test_a_staged_build_that_cannot_take_over_replaces_nothing(
    plan: ResourcePlan, check_rc: int, enrolled: bool, message: str
) -> None:
    host = AgentHost(check_rc=check_rc, enrolled=enrolled)
    drivers, control = _upgrade_setup(plan, host, [{"last_seen_at": _SEEN, "agent_version": OLD_VERSION}])

    with pytest.raises(ResourceReconcileError, match=message):
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert (host.binary, host.running, host.staged) == (OLD_SHA, OLD_SHA, "")
    host.assert_not_called_with_pattern("systemctl restart")
    assert control.tokens == []


def test_a_swap_whose_restart_was_lost_still_reads_as_the_old_build(plan: ResourcePlan) -> None:
    host = AgentHost()
    host.binary = BINARY.sha256  # the file was renamed in, but the old process still runs
    drivers, control = _upgrade_setup(plan, host, [{"last_seen_at": _SEEN, "agent_version": OLD_VERSION}])
    action = _action(plan, "agent:main")

    assert not drivers["agent_runtime"].observe(action, None).exists


def test_retrying_a_swap_whose_restart_was_lost_rolls_back_to_the_running_build(
    plan: ResourcePlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file already holds the new build; only the running image is the one to keep."""
    monkeypatch.setattr(agent_upgrade, "UPGRADE_REPORT_TIMEOUT", 0.0)
    host = AgentHost()
    host.binary = BINARY.sha256
    drivers, _control = _upgrade_setup(plan, host, [{"last_seen_at": _SEEN, "agent_version": OLD_VERSION}])

    with pytest.raises(ResourceReconcileError, match="the previous agent is back"):
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert (host.binary, host.running) == (OLD_SHA, OLD_SHA)


class _LostReply(FakeControl):
    """The service answers until the swap, then its gateway fails."""

    def __init__(self, host: AgentHost) -> None:
        super().__init__()
        self.host = host

    def node(self, node_id: str) -> NodeStatus | None:
        if self.host.running == BINARY.sha256:
            raise ControlServiceError("Control service returned HTTP 502")
        return super().node(node_id)


@pytest.mark.parametrize(
    ("swap_rc", "rollback_rc", "service_fails", "error", "binary"),
    [
        (1, 0, False, "failed: Could not swap in the new agent: restart failed; the previous agent is back", OLD_SHA),
        (0, 0, True, "HTTP 502; the previous agent is back", OLD_SHA),
        (255, 255, False, "Lost the server.*not restored", BINARY.sha256),
    ],
    ids=["restart-fails", "service-fails-while-polling", "server-lost"],
)
def test_a_failure_after_the_swap_puts_the_previous_agent_back(
    plan: ResourcePlan, swap_rc: int, rollback_rc: int, service_fails: bool, error: str, binary: str
) -> None:
    host = AgentHost(swap_rc=swap_rc, rollback_rc=rollback_rc)
    drivers, control, _cluster = _setup(plan, host, control=_LostReply(host) if service_fails else None)
    control.reports = [{"last_seen_at": _SEEN, "agent_version": OLD_VERSION}]

    with pytest.raises(ResourceReconcileError, match=error) as raised:
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert host.binary == binary
    # A lost server is not a definite failure: observation finds out which build runs.
    assert isinstance(raised.value, UnknownResourceOutcome) == (swap_rc == 255)


def test_a_dead_agent_of_an_old_build_is_enrolled_again_not_upgraded(plan: ResourcePlan) -> None:
    host = AgentHost(active=False)
    drivers, control = _upgrade_setup(plan, host, [{"last_seen_at": _SEEN}])

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    assert len(control.tokens) == 1
    assert AGENT_BOOTSTRAP in host.write_map
    host.assert_not_called_with_pattern(" check --bootstrap")
