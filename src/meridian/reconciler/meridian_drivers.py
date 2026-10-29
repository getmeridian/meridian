"""Drivers for Meridian's own control service and node agent (#95 walking skeleton).

The control service owns users and signs node documents; these drivers install
agents over SSH with a single-use enrollment token minted over the admin API,
and keep the service's node specs, endpoints, users, and settings equal to the
reviewed plan. A workload converges only when its agent
reports the current document applied with no failed resource.
"""

from __future__ import annotations

import json
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
from meridian.host_runtime import AGENT_BINARY, AGENT_BOOTSTRAP, AGENT_STATE_DIR, AGENT_UNIT
from meridian.reconciler.agent_spec import build_node_spec
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
from meridian.reconciler.workloads import WorkloadStateManager
from meridian.ssh import ServerConnection
from meridian.xray_workload import salamander_password

# An agent polls at least every 35 s (25 s wait plus margin); twice that is "seen".
AGENT_CONNECT_TIMEOUT = 90.0
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


def build_meridian_drivers(context: MeridianDriverContext) -> ResourceDrivers:
    return {
        "agent_runtime": AgentRuntimeDriver(context),
        "node_workload": NodeWorkloadDriver(context),
        "client_endpoint": ClientEndpointDriver(context),
        "control_user": ControlUserDriver(context),
        "service_settings": ServiceSettingsDriver(context),
    }


class AgentRuntimeDriver:
    """Install the agent binary, write its bootstrap with a fresh enrollment token, and run it.

    The agent generates its node key, enrolls with the token, and rewrites the
    bootstrap with the token nulled, so observation compares only the identity
    fields. Bare-IP control servers serve a Let's Encrypt IP certificate, so the
    agent verifies TLS through WebPKI and `tls_pin_sha256` stays null.
    """

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, AgentRuntimePayload)
        conn = self.context.connection_for(payload.server_ref)
        arch = _run(conn, "uname -m", "read the architecture").strip()
        binary = self.context.agent_binary(arch)
        installed = _run(conn, f"sha256sum {AGENT_BINARY} 2>/dev/null || true", "hash the agent").split()
        active = conn.run(f"systemctl is-active {AGENT_UNIT}", timeout=15).stdout.strip() == "active"
        bootstrap = conn.run(f"cat {AGENT_BOOTSTRAP} 2>/dev/null", timeout=15, sensitive=True).stdout
        with self.context.client() as client:
            ca_sha256 = client.fleet_authority().ca_sha256
            status = client.node(payload.workload_ref)
        expected = self._identity(payload, ca_sha256)
        current = _bootstrap_identity(bootstrap)
        in_place = bool(installed) and installed[0] == binary.sha256 and active and current == expected
        seen = status is not None and status.is_live()
        return _observation(action, exists=in_place, connected=in_place and seen, remote_id=payload.workload_ref)

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        payload = payload_of(action, AgentRuntimePayload)
        conn = self.context.connection_for(payload.server_ref)
        arch = _run(conn, "uname -m", "read the architecture").strip()
        binary = self.context.agent_binary(arch)
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

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not binding.server_ref:
            return
        conn = self.context.connection_for(binding.server_ref)
        conn.run(f"systemctl disable --now {AGENT_UNIT} >/dev/null 2>&1 || true", timeout=60)
        conn.run(
            f"rm -f /etc/systemd/system/{AGENT_UNIT} {AGENT_BINARY} {AGENT_BOOTSTRAP} && systemctl daemon-reload",
            timeout=30,
        )

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
    """Store the exit's node spec in the service and wait until its agent applied it."""

    def __init__(self, context: MeridianDriverContext) -> None:
        self.context = context

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        payload = payload_of(action, NodeWorkloadPayload)
        keys = self.context.reality_keys(payload.workload_ref, action.generation)
        if keys is None and _needs_keys(payload.profile):
            return _observation(action, exists=False, connected=False, remote_id="")
        spec = build_node_spec(payload.profile, keys)
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
        spec = build_node_spec(payload.profile, keys)
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
    return (
        status is not None
        and status.applied is not None
        and status.applied.revision == status.revision
        and status.applied.sha256 == status.sha256
        and not status.failed
    )


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
