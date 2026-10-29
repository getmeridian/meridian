"""Drivers for Meridian's own control service and node agent (#95 walking skeleton).

The control service owns users and signs node documents; these drivers install
agents over SSH with a single-use enrollment token minted over the admin API,
and keep the service's node specs, endpoints, users, and settings equal to the
reviewed plan. A workload converges only when its agent
reports the current document applied with no failed resource.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from meridian.cluster import ClusterConfig, ManagedResourceBinding, RealityKeyBinding
from meridian.compiler.models import (
    AgentRuntimePayload,
    ClientEndpointPayload,
    ConfigProfilePayload,
    ControlUserPayload,
    NodeWorkloadPayload,
    ResourcePlan,
    ServiceSettingsPayload,
)
from meridian.control.client import ControlClient
from meridian.control.models import ClientEndpoint, NodeSpec, NodeStatus, ServiceSettings
from meridian.control_artifacts import AgentBinary
from meridian.core.agent_protocol import AgentBootstrap
from meridian.core.agent_types import AGENT_PROTOCOL, canonical_sha256
from meridian.host_runtime import (
    AGENT_BINARY,
    AGENT_BOOTSTRAP,
    AGENT_CONFIG_DIR,
    AGENT_LIB_DIR,
    AGENT_STATE_DIR,
    AGENT_UNIT,
    CORE_UNIT,
)
from meridian.reconciler.agent_spec import build_node_spec
from meridian.reconciler.agent_upgrade import (
    PREVIOUS_BINARY,
    STAGED_BINARY,
    finish_upgrade,
    roll_back,
    running_sha256,
    stage_agent,
    swap_in,
    wait_for_upgrade,
)
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceDrivers,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    payload_of,
    postcondition_key,
)
from meridian.reconciler.server_evidence import require_available_evidence
from meridian.reconciler.server_support import ensure_warp, warp_connected
from meridian.reconciler.workloads import WorkloadStateManager
from meridian.ssh import ServerConnection
from meridian.xray_workload import ServiceRouteCredential, bridge_client_uuid, salamander_password

# An agent polls at least every 35 s (25 s wait plus margin); twice that is "seen".
AGENT_CONNECT_TIMEOUT = 90.0
# An agent that lost the service retries with a backoff of at most 60 s.
AGENT_RECONNECT_SECONDS = 75.0
# The first apply downloads the Xray archive (about 21 MB) before anything runs.
WORKLOAD_APPLY_TIMEOUT = 300.0
POLL_INTERVAL = 3.0


class ControlClientSource(Protocol):
    def __call__(self) -> ControlClient: ...


@dataclass
class MeridianDriverContext:
    plan: ResourcePlan
    cluster: ClusterConfig
    client: ControlClientSource
    workloads: WorkloadStateManager
    connection_for: Callable[[str], ServerConnection]
    server_addresses: Mapping[str, str]
    agent_binary: Callable[[str], AgentBinary]
    sleep: Callable[[float], None] = time.sleep

    def address(self, server_ref: str) -> str:
        address = self.server_addresses.get(server_ref, "")
        if not address:
            raise ResourceReconcileError(f"Server {server_ref} has no resolved address.", category="user")
        return address

    def service_url(self) -> str:
        if not self.cluster.panel.url:
            raise ResourceReconcileError("The control service URL is unknown before bootstrap.")
        return self.cluster.panel.url.rstrip("/")

    def reality_keys(self, workload_ref: str, generation: int) -> RealityKeyBinding | None:
        workload = self.workloads.find(workload_ref, generation)
        if workload is None:
            # A new generation keeps the keys an earlier one allocated; observation never allocates.
            candidates = sorted(
                (item for item in self.cluster.workloads if item.id == workload_ref and item.reality_keys),
                key=lambda item: item.generation,
            )
            workload = candidates[-1] if candidates else None
        if workload is None or not workload.reality_keys:
            return None
        return next(iter(workload.reality_keys.values()))

    def service_credentials(
        self, profile: ConfigProfilePayload, generation: int
    ) -> dict[str, ServiceRouteCredential] | None:
        """A gateway's edge credentials, from each target exit's keys; None while one has none yet.

        The exit renders the same derived ID as its bridge's only client (`agent_spec`), so no
        service user, squad, or stored secret stands between them.
        """
        credentials: dict[str, ServiceRouteCredential] = {}
        for edge in profile.service_outbounds:
            keys = self.reality_keys(edge.target_workload_ref, generation)
            if keys is None:
                return None
            credentials[edge.edge_id] = ServiceRouteCredential(
                address=self.address(edge.target_server_ref),
                vless_uuid=bridge_client_uuid(keys, edge.target_inbound_ref),
                public_key=keys.public_key,
                short_id=keys.short_id,
            )
        return credentials


def build_meridian_drivers(context: MeridianDriverContext) -> ResourceDrivers:
    return {
        "agent_runtime": AgentRuntimeDriver(context),
        "node_workload": NodeWorkloadDriver(context),
        "client_endpoint": ClientEndpointDriver(context),
        "control_user": ControlUserDriver(context),
        "service_settings": ServiceSettingsDriver(context),
    }


@dataclass(frozen=True)
class AgentInspection:
    """What one look at a node's agent found."""

    current_build: bool  # the unit's process runs this Meridian's agent build
    active: bool
    identity: bool  # the bootstrap names this node, service, and CA
    live: bool  # the service saw the agent recently

    @property
    def in_place(self) -> bool:
        return self.current_build and self.active and self.identity


class AgentRuntimeDriver:
    """Install the agent binary, write its bootstrap with a fresh enrollment token, and run it.

    WARP, when the exit uses it, is installed over SSH first, exactly as on a panel node: it is
    an apt package with its own daemon and registration, outside the agent's resource kinds.

    The agent generates its node key, enrolls with the token, and rewrites the
    bootstrap with the token nulled, so observation compares only the identity
    fields. A healthy agent of another build is upgraded in place
    (`agent_upgrade.py`): same key, certificate, and bootstrap, and the previous
    binary back if the new one never reports. Only a missing, foreign, or dead
    agent is enrolled again.

    TLS on 443 is always WebPKI: the bootstrap fails rather than keep a self-signed
    certificate, and the CLI never sends credentials over one either, so
    `tls_pin_sha256` stays null and the agent refuses a pin. Pinning would also
    break on every renewal of a 6-day IP certificate, while the fleet CA already
    authenticates the service end to end (signed documents, node proofs).
    """

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, AgentRuntimePayload)
        inspection = self._inspect(payload)
        in_place = inspection.in_place
        if in_place and payload.warp:
            in_place = warp_connected(action.resource.logical_id, self.context.connection_for(payload.server_ref))
        return _observation(
            action, exists=in_place, connected=in_place and inspection.live, remote_id=payload.workload_ref
        )

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        payload = payload_of(action, AgentRuntimePayload)
        conn = self.context.connection_for(payload.server_ref)
        if payload.warp:
            ensure_warp(conn, self.context.address(payload.server_ref))
        inspection = self._inspect(payload)
        # A service outage leaves a healthy agent unseen until its backoff ends; wait it out
        # rather than re-enroll (or refuse to upgrade) a node that is fine.
        healthy = inspection.active and inspection.identity and (inspection.live or self._reconnects(payload))
        if healthy and inspection.current_build:
            return ResourceApplyReceipt(remote_id=payload.workload_ref)  # only WARP needed repair
        arch = _run(conn, "uname -m", "read the architecture").strip()
        binary = self.context.agent_binary(arch)
        if healthy:
            self._upgrade(conn, payload, binary)
            return ResourceApplyReceipt(remote_id=payload.workload_ref)
        _ok(conn.put_bytes(AGENT_BINARY, binary.data, mode="755", timeout=180), "upload the agent")
        with self.context.client() as client:
            grant = client.grant_enrollment(payload.workload_ref)
            bootstrap = AgentBootstrap(
                service_url=self.context.service_url(),
                fleet_id=self.context.cluster.fleet_id,
                node_id=payload.workload_ref,
                enrollment_token=grant.enrollment_token,
                token_expires_at=grant.token_expires_at,
                ca_sha256=grant.ca_sha256,
                tls_pin_sha256=None,
            )
            body = json.dumps(bootstrap.model_dump(mode="json"), indent=2) + "\n"
            _ok(
                conn.put_text(AGENT_BOOTSTRAP, body, mode="600", create_parent=True, sensitive=True, timeout=15),
                "write the agent bootstrap",
            )
            _ok(conn.put_text(f"/etc/systemd/system/{AGENT_UNIT}", _agent_unit(), mode="644", timeout=15), "write unit")
            started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            result = conn.run(
                f"install -d -m 700 {AGENT_STATE_DIR} && systemctl daemon-reload && "
                f"systemctl enable {AGENT_UNIT} >/dev/null 2>&1 && systemctl restart {AGENT_UNIT}",
                timeout=60,
            )
            if result.returncode == 124:
                raise UnknownResourceOutcome(f"Timed out starting {AGENT_UNIT}; observation is required.")
            _ok(result, f"start {AGENT_UNIT}")
            deadline = time.monotonic() + AGENT_CONNECT_TIMEOUT
            while True:
                status = client.node(payload.workload_ref)
                if status is not None and status.last_seen_at and status.last_seen_at >= started_at:
                    break
                if time.monotonic() >= deadline:
                    logs = conn.run(f"journalctl -u {AGENT_UNIT} -n 5 --no-pager -o cat", timeout=15).stdout
                    raise ResourceReconcileError(
                        f"The node agent on {payload.server_ref} did not reach the control service.",
                        hint=f"Its last log lines: {logs.strip()[-300:]}",
                    )
                self.context.sleep(POLL_INTERVAL)
        return ResourceApplyReceipt(remote_id=payload.workload_ref)

    def _upgrade(self, conn: ServerConnection, payload: AgentRuntimePayload, binary: AgentBinary) -> None:
        """Swap a healthy agent's binary for this build, keeping its identity; roll back if it never reports."""
        staged = stage_agent(conn, binary, payload.workload_ref)
        _ok(conn.put_text(f"/etc/systemd/system/{AGENT_UNIT}", _agent_unit(), mode="644", timeout=15), "write unit")
        failure: Exception | None = None
        with self.context.client() as client:
            before = client.node(payload.workload_ref)
            try:
                swap_in(conn)
                upgraded = wait_for_upgrade(
                    conn,
                    client,
                    payload.workload_ref,
                    staged=staged,
                    binary=binary,
                    before=before,
                    sleep=self.context.sleep,
                    interval=POLL_INTERVAL,
                )
            except Exception as exc:  # noqa: BLE001 - any failure after the swap puts the previous agent back
                failure, upgraded = exc, False
            if upgraded:
                finish_upgrade(conn)
                return
        logs = conn.run(f"journalctl -u {AGENT_UNIT} -n 5 --no-pager -o cat", timeout=15).stdout.strip()[-300:]
        what = f"failed: {failure}" if failure is not None else f"never reported {staged.version}"
        if not roll_back(conn):
            message = f"The upgraded agent on {payload.server_ref} {what}, and the previous one was not restored."
            hint = f"On the server: mv -f {PREVIOUS_BINARY} {AGENT_BINARY} && systemctl restart {AGENT_UNIT}"
            if isinstance(failure, UnknownResourceOutcome):
                # The server was lost mid-swap; observation finds out which build runs now.
                raise UnknownResourceOutcome(f"{message} If it stays stopped: {hint}") from failure
            raise ResourceReconcileError(message, hint=hint) from failure
        raise ResourceReconcileError(
            f"The upgraded agent on {payload.server_ref} {what}; the previous agent is back.",
            hint=f"Its last log lines: {logs}",
        ) from failure

    def _inspect(self, payload: AgentRuntimePayload) -> AgentInspection:
        """What runs on the node, under which identity, and whether the service sees it live."""
        conn = self.context.connection_for(payload.server_ref)
        arch = _run(conn, "uname -m", "read the architecture").strip()
        binary = self.context.agent_binary(arch)
        # The running image, not the file: a swap whose restart was lost still runs the old build.
        running = running_sha256(conn)
        # A lost connection or a denied read is unavailable evidence, never a missing agent:
        # reading it as absence would re-key a healthy node.
        logical_id = f"agent:{payload.workload_ref}"
        unit = conn.run(f"systemctl is-active {AGENT_UNIT}", timeout=15)
        require_available_evidence(logical_id, unit, f"the {AGENT_UNIT} unit")
        active = unit.stdout.strip() == "active"
        read = conn.run(f"cat {AGENT_BOOTSTRAP}", timeout=15, sensitive=True)
        require_available_evidence(logical_id, read, "the agent bootstrap")
        bootstrap = read.stdout if read.returncode == 0 else ""
        with self.context.client() as client:
            ca_sha256 = client.fleet_authority().ca_sha256
            status = client.node(payload.workload_ref)
        return AgentInspection(
            current_build=running == binary.sha256,
            active=active,
            identity=_bootstrap_identity(bootstrap) == self._identity(payload, ca_sha256),
            live=status is not None and status.is_live(),
        )

    def _reconnects(self, payload: AgentRuntimePayload) -> bool:
        with self.context.client() as client:
            for _ in range(int(AGENT_RECONNECT_SECONDS / POLL_INTERVAL)):
                self.context.sleep(POLL_INTERVAL)
                status = client.node(payload.workload_ref)
                if status is not None and status.is_live():
                    return True
        return False

    def retire(self, binding: ManagedResourceBinding) -> None:
        """Stop the agent and the core it runs, and delete both with their keys and state.

        A plan that still runs an agent on the server (an exit renamed in place) keeps it;
        the old binding is only forgotten. WARP stays, as on panel nodes.
        """
        if not binding.server_ref:
            return
        if any(
            isinstance(resource.payload, AgentRuntimePayload) and resource.payload.server_ref == binding.server_ref
            for resource in self.context.plan.resources
        ):
            return
        conn = self.context.connection_for(binding.server_ref)
        units = f"{AGENT_UNIT} {CORE_UNIT}"
        # One unit per systemctl call: a multi-unit `disable --now` stops nothing when one is missing.
        stop_each = f'for unit in {units}; do systemctl disable --now "$unit" >/dev/null 2>&1; done;'
        paths = " ".join(
            shlex.quote(path)
            for path in (
                f"/etc/systemd/system/{AGENT_UNIT}",
                f"/etc/systemd/system/{CORE_UNIT}",
                AGENT_BINARY,
                STAGED_BINARY,
                PREVIOUS_BINARY,
                AGENT_CONFIG_DIR,
                AGENT_LIB_DIR,
                AGENT_STATE_DIR,
            )
        )
        result = conn.run(
            f"{stop_each} rm -rf {paths} && systemctl daemon-reload && {stop_each}"
            f" ! systemctl is-active --quiet {AGENT_UNIT} && ! systemctl is-active --quiet {CORE_UNIT}",
            timeout=60,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"The node agent on {binding.server_ref} is still running after removal.")

    def _identity(self, payload: AgentRuntimePayload, ca_sha256: str) -> dict[str, Any]:
        return {
            "protocol": AGENT_PROTOCOL,
            "service_url": self.context.service_url(),
            "fleet_id": self.context.cluster.fleet_id,
            "node_id": payload.workload_ref,
            "ca_sha256": ca_sha256,
            "tls_pin_sha256": None,
        }


class NodeWorkloadDriver:
    """Store an exit's or gateway's node spec in the service and wait until its agent applied it."""

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, NodeWorkloadPayload)
        keys = self.context.reality_keys(payload.workload_ref, action.generation)
        credentials = self.context.service_credentials(payload.profile, action.generation)
        if (keys is None and _needs_keys(payload.profile)) or credentials is None:
            return _observation(action, exists=False, connected=False, remote_id="")
        spec = build_node_spec(payload.profile, keys, credentials)
        with self.context.client() as client:
            status = client.node(payload.workload_ref)
        stored = status is not None and status.spec_sha256 == _spec_hash(spec)
        return _observation(
            action, exists=stored, connected=stored and _applied(status), remote_id=payload.workload_ref
        )

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        payload = payload_of(action, NodeWorkloadPayload)
        workload = self.context.workloads.ensure(
            payload.profile,
            generation=action.generation,
            desired_hash=action.expected_hash,
            server_ref=payload.server_ref,
        )
        keys = next(iter(workload.reality_keys.values()), None)
        credentials = self.context.service_credentials(payload.profile, action.generation)
        if credentials is None:
            raise ResourceReconcileError(
                f"Routing gateway {payload.workload_ref} dials an exit that has no Reality keys yet.",
                hint="Apply the plan again; the exit workloads it routes to come first.",
            )
        spec = build_node_spec(payload.profile, keys, credentials)
        with self.context.client() as client:
            status = client.put_node(payload.workload_ref, spec)
            deadline = time.monotonic() + WORKLOAD_APPLY_TIMEOUT
            while not _applied(status):
                if status.failed and status.applied is None:
                    raise ResourceReconcileError(
                        f"The agent on {payload.server_ref} could not apply its workload: " + "; ".join(status.errors)
                    )
                if time.monotonic() >= deadline:
                    detail = "; ".join(status.errors) or "no report of the new revision yet"
                    raise ResourceReconcileError(
                        f"The agent on {payload.server_ref} did not apply revision {status.revision}: {detail}",
                        hint="Check `journalctl -u netagent` on the server, then rerun `meridian apply`.",
                    )
                self.context.sleep(POLL_INTERVAL)
                current = client.node(payload.workload_ref)
                if current is None:
                    raise ResourceReconcileError(f"The control service lost node {payload.workload_ref}.")
                status = current
        return ResourceApplyReceipt(remote_id=payload.workload_ref)

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not binding.remote_id:
            return
        with self.context.client() as client:
            client.delete_node(binding.remote_id)


class ClientEndpointDriver:
    """Publish one endpoint, with the workload's public Reality identity, to subscriptions."""

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, ClientEndpointPayload)
        expected = self._endpoint(payload, action.generation)
        with self.context.client() as client:
            current = client.get_endpoint(_service_id(action.resource.logical_id))
        exists = expected is not None and current == expected
        return _observation(action, exists=exists, connected=False, remote_id=_service_id(action.resource.logical_id))

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        payload = payload_of(action, ClientEndpointPayload)
        endpoint = self._endpoint(payload, action.generation)
        if endpoint is None:
            raise ResourceReconcileError(f"Workload {payload.workload_ref} has no Reality keys yet.")
        endpoint_id = _service_id(action.resource.logical_id)
        with self.context.client() as client:
            client.put_endpoint(endpoint_id, endpoint)
        return ResourceApplyReceipt(remote_id=endpoint_id)

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id:
            with self.context.client() as client:
                client.delete_endpoint(binding.remote_id)

    def _endpoint(self, payload: ClientEndpointPayload, generation: int) -> ClientEndpoint | None:
        keys = self.context.reality_keys(payload.workload_ref, generation)
        if keys is None and payload.protocol in {"reality", "hysteria2"}:
            return None
        address = payload.address or self.context.address(payload.address_server_ref)
        return ClientEndpoint(
            remark=payload.remark,
            protocol=payload.protocol,
            address=address,
            port=payload.public_port,
            position=payload.position,
            fingerprint=payload.fingerprint,
            reality_sni=payload.sni if payload.protocol == "reality" else "",
            reality_public_key=keys.public_key if keys and payload.protocol == "reality" else "",
            reality_short_id=keys.short_id if keys and payload.protocol == "reality" else "",
            tls_sni="" if payload.protocol == "reality" else payload.sni,
            host=payload.host,
            path=payload.path,
            salamander_password=salamander_password(keys) if keys and payload.protocol == "hysteria2" else "",
            fingerprint_copy=payload.fingerprint_copy,
        )


class ControlUserDriver:
    """Create the user in the service; its credentials and status are the service's."""

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, ControlUserPayload)
        with self.context.client() as client:
            user = client.get_user(payload.username)
        return _observation(action, exists=user is not None, connected=False, remote_id=payload.username)

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        payload = payload_of(action, ControlUserPayload)
        with self.context.client() as client:
            client.put_user(payload.username)
        return ResourceApplyReceipt(remote_id=payload.username)

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id:
            with self.context.client() as client:
                client.delete_user(binding.remote_id)


class ServiceSettingsDriver:
    """Keep the subscription title and the public base its links use (from the bootstrap, not the plan)."""

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        with self.context.client() as client:
            current = client.settings()
        return _observation(action, exists=current == self._settings(action), connected=False, remote_id="settings")

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        with self.context.client() as client:
            client.put_settings(self._settings(action))
        return ResourceApplyReceipt(remote_id="settings")

    def _settings(self, action: ResourceAction) -> ServiceSettings:
        payload = payload_of(action, ServiceSettingsPayload)
        return ServiceSettings(
            profile_title=payload.profile_title, public_url=self.context.cluster.panel.public_base_url()
        )


def _observation(action: ResourceAction, *, exists: bool, connected: bool, remote_id: str) -> ResourceObservation:
    satisfied = sorted(
        postcondition_key(item.kind, item.target_ref, item.detail)
        for item in action.resource.postconditions
        if exists and (item.kind != "connected" or connected)
    )
    return ResourceObservation(
        exists=exists,
        observed_hash=action.expected_hash if exists else "",
        remote_id=remote_id if exists else "",
        satisfied_postconditions=satisfied,
    )


def _applied(status: NodeStatus | None) -> bool:
    return status is not None and status.in_sync and not status.failed


def _spec_hash(spec: NodeSpec) -> str:
    return canonical_sha256(spec.model_dump(mode="json"))


def _needs_keys(profile: ConfigProfilePayload) -> bool:
    return any(inbound.protocol in {"reality", "hysteria2"} for inbound in profile.inbounds)


def _service_id(logical_id: str) -> str:
    return logical_id.removeprefix("endpoint:")


_IDENTITY_FIELDS = ("protocol", "service_url", "fleet_id", "node_id", "ca_sha256", "tls_pin_sha256")


def _bootstrap_identity(text: str) -> dict[str, Any]:
    """The bootstrap fields that name the node and its service; the token is spent once enrolled."""
    try:
        data: Any = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict) or not set(_IDENTITY_FIELDS) <= set(data):
        return {}
    return {name: data[name] for name in _IDENTITY_FIELDS}


def _agent_unit() -> str:
    return (
        "[Unit]\n"
        "Description=Network agent\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={AGENT_BINARY} run --bootstrap {AGENT_BOOTSTRAP} --state-dir {AGENT_STATE_DIR}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _run(conn: ServerConnection, command: str, what: str) -> str:
    result = conn.run(command, timeout=30)
    if result.returncode != 0:
        raise ResourceReconcileError(f"Could not {what}: {result.stderr.strip()[:200]}", retryable=True)
    return result.stdout


def _ok(result: Any, what: str) -> None:
    if result.returncode == 124:
        raise UnknownResourceOutcome(f"Timed out while trying to {what}; observation is required.")
    if result.returncode != 0:
        raise ResourceReconcileError(f"Could not {what}: {result.stderr.strip()[:200]}")
