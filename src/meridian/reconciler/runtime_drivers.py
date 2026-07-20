"""Truthful control-plane and post-apply probe resource drivers."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    AccessUserPayload,
    ControlPlaneRuntimePayload,
    HostPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
    ProbePayload,
    ResourcePlan,
    RoutingGatewayPayload,
)
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.remnawave import XRAY_JSON_CLIENT_TYPE
from meridian.ssh import ServerConnection


class RuntimePanel(Protocol):
    """Panel reads needed by runtime probes."""

    def ping(self) -> bool: ...

    def get_node(self, uuid: str) -> Any | None: ...

    def get_user(self, username: str) -> Any | None: ...

    def fetch_subscription(self, short_uuid: str, *, client_type: str = "") -> Any: ...


@dataclass(frozen=True)
class ControlPlaneDriverContext:
    """Late-bound control bootstrap operations owned by setup runtime."""

    ready: Callable[[ResourceAction], bool]
    bootstrap: Callable[[ResourceAction], None]


@dataclass(frozen=True)
class ProbeDriverContext:
    """Observed state required to attest compiled probe resources."""

    plan: ResourcePlan
    cluster: ClusterConfig
    panel: RuntimePanel
    connection_for: Callable[[str], ServerConnection]
    server_addresses: Mapping[str, str]


class ControlPlaneRuntimeDriver:
    """Checkpoint control bootstrap and require a responsive panel afterward."""

    def __init__(self, context: ControlPlaneDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        _control_payload(action)
        ready = self.context.ready(action)
        return _probe_observation(
            action,
            passed=ready,
            remote_id=(binding.remote_id if binding is not None else action.resource.logical_id),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        _control_payload(action)
        try:
            self.context.bootstrap(action)
        except UnknownResourceOutcome:
            raise
        except Exception as exc:
            if _exception_has_timeout(exc):
                raise UnknownResourceOutcome(
                    f"Timed out while bootstrapping {action.resource.logical_id}; observation is required."
                ) from exc
            raise
        return ResourceApplyReceipt(remote_id=action.resource.logical_id)


class ProbeDriver:
    """Execute concrete panel and server observations for compiled probes."""

    def __init__(self, context: ProbeDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = action.resource.payload
        if not isinstance(payload, ProbePayload):
            raise ResourceReconcileError(f"Probe driver received {payload.kind}.")
        passed = self._run(payload, action.generation)
        return _probe_observation(
            action,
            passed=passed,
            remote_id=payload.target_ref,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = action.resource.payload
        if not isinstance(payload, ProbePayload):
            raise ResourceReconcileError(f"Probe driver received {payload.kind}.")
        if not self._run(payload, action.generation):
            raise ResourceReconcileError(
                f"{payload.probe.replace('_', ' ').title()} probe failed for {payload.target_ref!r}."
            )
        return ResourceApplyReceipt(remote_id=payload.target_ref)

    def _run(self, payload: ProbePayload, generation: int) -> bool:
        if payload.probe == "node":
            return self._node_connected(payload.target_ref, generation)
        if payload.probe == "listener":
            return self._listener_active(payload.target_ref)
        if payload.probe == "subscription":
            return self._subscription_available(payload.target_ref)
        if payload.probe == "route":
            return self._gateway_active(payload.target_ref, generation)
        raise ResourceReconcileError(f"Probe kind {payload.probe!r} has no truthful runtime implementation.")

    def _node_connected(self, target_ref: str, generation: int) -> bool:
        runtime = self._payload(target_ref, NodeRuntimePayload)
        self._payload(runtime.binding_ref, NodeBindingPayload)
        remote = self._binding(binding_ref=runtime.binding_ref, generation=generation)
        node = self.context.panel.get_node(remote.remote_id) if remote and remote.remote_id else None
        return bool(node is not None and not node.is_disabled and node.is_connected)

    def _listener_active(self, target_ref: str) -> bool:
        host = self._payload(target_ref, HostPayload)
        conn = self.context.connection_for(host.address_server_ref)
        transport = "udp" if host.protocol == "hysteria2" else "tcp"
        flag = "u" if transport == "udp" else "t"
        result = conn.run(f"ss -H -ln{flag} 2>/dev/null", timeout=15)
        return result.returncode == 0 and _port_in_ss(result.stdout, host.public_port)

    def _subscription_available(self, target_ref: str) -> bool:
        user_payload = self._payload(target_ref, AccessUserPayload)
        user = self.context.panel.get_user(user_payload.username)
        if user is None or not user.short_uuid:
            return False
        document = self.context.panel.fetch_subscription(user.short_uuid, client_type=XRAY_JSON_CLIENT_TYPE)
        return xray_subscription_is_valid(document)

    def _gateway_active(self, target_ref: str, generation: int) -> bool:
        gateway = self._payload(target_ref, RoutingGatewayPayload)
        if not self._node_connected(gateway.node_ref, generation):
            return False
        profile_binding = self._binding(
            binding_ref=gateway.profile_ref,
            generation=generation,
        )
        runtime = self._payload(gateway.node_ref, NodeRuntimePayload)
        node_binding = self._binding(
            binding_ref=runtime.binding_ref,
            generation=generation,
        )
        if profile_binding is None or node_binding is None:
            return False
        node = self.context.panel.get_node(node_binding.remote_id)
        return bool(
            node is not None and node.is_connected and node.active_config_profile_uuid == profile_binding.remote_id
        )

    def _binding(
        self,
        *,
        binding_ref: str,
        generation: int,
    ) -> ManagedResourceBinding | None:
        return self.context.cluster.managed_bindings.get(f"{binding_ref}@{generation}")

    def _payload(self, logical_id: str, expected: type[Any]) -> Any:
        for resource in self.context.plan.resources:
            if resource.logical_id == logical_id:
                if not isinstance(resource.payload, expected):
                    raise ResourceReconcileError(
                        f"Probe target {logical_id!r} is {resource.payload.kind}, expected {expected.__name__}."
                    )
                return resource.payload
        raise ResourceReconcileError(f"Probe target {logical_id!r} is not in the reviewed plan.")


def xray_subscription_is_valid(document: Any) -> bool:
    """Require a canonical Xray document with at least one managed proxy."""
    if not document.url or not document.content.strip():
        return False
    try:
        parsed = json.loads(document.content)
    except (TypeError, json.JSONDecodeError):
        return False
    if isinstance(parsed, list):
        if len(parsed) != 1:
            return False
        parsed = parsed[0]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("outbounds"), list):
        return False
    return any(
        isinstance(outbound, dict)
        and str(outbound.get("tag", "")).startswith("MERIDIAN_PROXY")
        and (
            outbound.get("protocol") == "vless"
            or (
                outbound.get("protocol") == "hysteria"
                and isinstance(outbound.get("settings"), dict)
                and outbound["settings"].get("version") == 2
            )
        )
        for outbound in parsed["outbounds"]
    )


def _probe_observation(
    action: ResourceAction,
    *,
    passed: bool,
    remote_id: str,
) -> ResourceObservation:
    satisfied = (
        sorted(postcondition_key(item.kind, item.target_ref, item.detail) for item in action.resource.postconditions)
        if passed
        else []
    )
    return ResourceObservation(
        exists=passed,
        observed_hash=action.expected_hash if passed else "",
        remote_id=remote_id if passed else "",
        satisfied_postconditions=satisfied,
    )


def _control_payload(action: ResourceAction) -> ControlPlaneRuntimePayload:
    payload = action.resource.payload
    if not isinstance(payload, ControlPlaneRuntimePayload):
        raise ResourceReconcileError(f"Control runtime driver received {payload.kind}.")
    return payload


def _exception_has_timeout(error: Exception) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        detail = f"{current} {getattr(current, 'hint', '')}".casefold()
        if "timed out" in detail or "timeout" in detail:
            return True
        current = current.__cause__ or current.__context__
    return False


def _port_in_ss(output: str, port: int) -> bool:
    suffix = f":{shlex.quote(str(port))}"
    return any(field.endswith(suffix) for line in output.splitlines() for field in line.split())
