"""Concrete Remnawave drivers for V4 workload control-plane resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressPoolPayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
    ResourcePlan,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServiceUserPayload,
    canonical_hash,
)
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceDrivers,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.reconciler.routing_credentials import resolve_service_credentials
from meridian.reconciler.workloads import WorkloadStateManager
from meridian.remnawave import (
    ConfigProfile,
    Host,
    Inbound,
    InternalSquad,
    MeridianPanel,
    Node,
    RemnawaveNetworkError,
)
from meridian.xray_workload import render_workload_config

PayloadT = TypeVar(
    "PayloadT",
    ConfigProfilePayload,
    InboundPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
    HostPayload,
    InternalSquadPayload,
    ServiceUserPayload,
    EgressPoolPayload,
    RouteRulePayload,
    RoutingGatewayPayload,
)
ValueT = TypeVar("ValueT")


@dataclass
class RemnawaveDriverContext:
    panel: MeridianPanel
    plan: ResourcePlan
    cluster: ClusterConfig
    workloads: WorkloadStateManager
    server_addresses: Mapping[str, str]
    node_secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def resource_payload(self, logical_id: str, expected_type: type[PayloadT]) -> PayloadT:
        resource = next(
            (item for item in self.plan.resources if item.logical_id == logical_id),
            None,
        )
        if resource is None or not isinstance(resource.payload, expected_type):
            raise ResourceReconcileError(f"Reviewed dependency {logical_id} is missing or has the wrong resource kind.")
        return resource.payload

    def remote_id(self, logical_id: str, generation: int) -> str:
        binding = self.cluster.managed_bindings.get(f"{logical_id}@{generation}")
        return binding.remote_id if binding is not None else ""

    def address(self, server_ref: str) -> str:
        address = self.server_addresses.get(server_ref, "")
        if not address:
            raise ResourceReconcileError(
                f"Server {server_ref} has no resolved address.",
                hint="Validate the saved server before applying this topology.",
                category="user",
            )
        return address

    def was_managed(self, logical_id: str, remote_id: str) -> bool:
        if any(
            binding.logical_id == logical_id and binding.remote_id == remote_id
            for binding in self.cluster.managed_bindings.values()
        ):
            return True
        return any(
            remote_id
            in {
                workload.config_profile_uuid,
                *workload.node_uuids.values(),
                *workload.host_uuids.values(),
            }
            for workload in self.cluster.workloads
        )


def build_remnawave_drivers(context: RemnawaveDriverContext) -> ResourceDrivers:
    """Build the finite Remnawave subset used by exit workloads."""
    from meridian.reconciler.delivery_drivers import build_delivery_drivers
    from meridian.reconciler.routing_drivers import (
        ProfileProjectionDriver,
        ServiceUserDriver,
    )

    drivers: ResourceDrivers = {
        "config_profile": ConfigProfileDriver(context),
        "inbound": InboundDriver(context),
        "node_binding": NodeBindingDriver(context),
        "host": HostDriver(context),
        "internal_squad": InternalSquadDriver(context),
        "service_user": ServiceUserDriver(context),
        "egress_pool": ProfileProjectionDriver(context),
        "route_rule": ProfileProjectionDriver(context),
        "routing_gateway": ProfileProjectionDriver(context),
    }
    drivers.update(cast(ResourceDrivers, build_delivery_drivers(context)))
    return drivers


class ConfigProfileDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, ConfigProfilePayload)
        profile = _bound_or_named_profile(self.context, payload, binding)
        if profile is None:
            return ResourceObservation(exists=False)
        server_ref = _profile_server_ref(self.context, payload)
        workload = self.context.workloads.require(payload.workload_id, action.generation)
        expected_config = render_workload_config(
            payload,
            reality_keys=workload.reality_keys.get(server_ref),
            service_credentials=resolve_service_credentials(
                self.context,
                payload,
                action.generation,
            ),
        )
        matches = profile.name == payload.name and profile.config == expected_config
        owned = binding is not None or self.context.was_managed(action.resource.logical_id, profile.uuid)
        if profile.uuid and (matches or owned):
            self.context.workloads.record_profile_uuid(
                payload.workload_id,
                action.generation,
                profile.uuid,
            )
        return _observed(action, profile.uuid, matches, _profile_projection(profile))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, ConfigProfilePayload)
        server_ref = _profile_server_ref(self.context, payload)
        workload = self.context.workloads.ensure(
            payload,
            generation=action.generation,
            desired_hash=action.expected_hash,
            server_ref=server_ref,
        )
        expected_config = render_workload_config(
            payload,
            reality_keys=workload.reality_keys.get(server_ref),
            service_credentials=resolve_service_credentials(
                self.context,
                payload,
                action.generation,
            ),
        )
        existing = _bound_or_named_profile(self.context, payload, binding)
        if existing is None:
            profile = _mutation(
                lambda: self.context.panel.create_config_profile(payload.name, expected_config),
                f"create Profile {payload.name}",
            )
        else:
            if binding is None and not self.context.was_managed(action.resource.logical_id, existing.uuid):
                raise ResourceReconcileError(
                    f"Unmanaged Remnawave Profile collides with owned name {payload.name!r}.",
                    hint="Rename the unmanaged Profile or explicitly recover its Meridian binding.",
                    category="user",
                )
            profile = _mutation(
                lambda: self.context.panel.update_config_profile(
                    existing.uuid,
                    name=payload.name,
                    config=expected_config,
                ),
                f"update Profile {payload.name}",
            )
        return ResourceApplyReceipt(remote_id=profile.uuid)


class InboundDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, InboundPayload)
        profile_ref = _profile_ref_for_workload(self.context, payload.workload_ref)
        profile_uuid = self.context.remote_id(profile_ref, action.generation)
        if not profile_uuid:
            workload = self.context.workloads.require(payload.workload_ref, action.generation)
            profile_uuid = workload.config_profile_uuid
        if not profile_uuid:
            return ResourceObservation(exists=False)
        profile = self.context.panel.get_config_profile(profile_uuid)
        if profile is None:
            return ResourceObservation(exists=False)
        matches = [inbound for inbound in profile.inbounds if inbound.tag == payload.tag]
        if len(matches) > 1:
            raise ResourceReconcileError(
                f"Profile {profile.name!r} contains duplicate Inbounds tagged {payload.tag!r}."
            )
        if not matches:
            return ResourceObservation(exists=False)
        inbound = matches[0]
        converged = _inbound_matches(inbound, payload, profile_uuid)
        if inbound.uuid:
            self.context.workloads.record_inbound_uuid(
                payload.workload_ref,
                action.generation,
                action.resource.logical_id,
                inbound.uuid,
            )
        return _observed(action, inbound.uuid, converged, _inbound_projection(inbound))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, InboundPayload)
        raise ResourceReconcileError(
            f"Profile {payload.workload_ref!r} did not materialize reviewed Inbound {payload.tag!r}.",
            hint="Inspect the Profile response; Inbounds are created atomically with their Profile.",
        )


class NodeBindingDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, NodeBindingPayload)
        node = _bound_or_named_node(self.context, payload, binding)
        if node is None:
            return ResourceObservation(exists=False)
        profile_uuid, inbound_uuids = _resolved_node_dependencies(
            self.context,
            payload,
            action.generation,
        )
        address = self.context.address(payload.server_ref)
        api_port = _node_api_port(self.context, payload)
        matches = (
            node.name == payload.name
            and node.address == address
            and node.port == api_port
            and not node.is_disabled
            and node.active_config_profile_uuid == profile_uuid
            and sorted(node.active_inbound_uuids) == sorted(inbound_uuids)
        )
        owned = binding is not None or self.context.was_managed(action.resource.logical_id, node.uuid)
        if node.uuid and (matches or owned):
            self.context.workloads.record_node_uuid(
                payload.workload_ref,
                action.generation,
                payload.server_ref,
                node.uuid,
            )
        return _observed(action, node.uuid, matches, _node_projection(node))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, NodeBindingPayload)
        profile_uuid, inbound_uuids = _resolved_node_dependencies(
            self.context,
            payload,
            action.generation,
        )
        address = self.context.address(payload.server_ref)
        api_port = _node_api_port(self.context, payload)
        existing = _bound_or_named_node(self.context, payload, binding)
        if existing is None:
            credentials = _mutation(
                lambda: self.context.panel.create_node(
                    payload.name,
                    address,
                    api_port,
                    config_profile_uuid=profile_uuid,
                    inbound_uuids=inbound_uuids,
                ),
                f"create Node {payload.name}",
            )
            if not credentials.uuid or not credentials.secret_key:
                raise ResourceReconcileError(f"Remnawave returned incomplete credentials for Node {payload.name!r}.")
            self.context.node_secrets[payload.workload_ref] = credentials.secret_key
            return ResourceApplyReceipt(remote_id=credentials.uuid)
        if binding is None and not self.context.was_managed(action.resource.logical_id, existing.uuid):
            raise ResourceReconcileError(
                f"Unmanaged Remnawave Node collides with owned name {payload.name!r}.",
                hint="Rename the unmanaged Node or explicitly recover its Meridian binding.",
                category="user",
            )
        if existing.address != address or existing.port != api_port:
            raise ResourceReconcileError(
                f"Node {payload.name!r} address or API port drift requires explicit replacement."
            )
        _mutation(
            lambda: self.context.panel.bind_node_profile(
                existing.uuid,
                profile_uuid,
                inbound_uuids,
            ),
            f"bind Node {payload.name}",
        )
        if existing.name != payload.name:
            _mutation(
                lambda: self.context.panel.update_node_name(existing.uuid, payload.name),
                f"rename Node {payload.name}",
            )
        if existing.is_disabled:
            _mutation(
                lambda: self.context.panel.enable_node(existing.uuid),
                f"enable Node {payload.name}",
            )
        return ResourceApplyReceipt(remote_id=existing.uuid)


class HostDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, HostPayload)
        host = _bound_or_named_host(self.context, payload, binding)
        if host is None:
            return ResourceObservation(exists=False)
        profile_uuid, inbound_uuid, workload_id = _resolved_host_dependencies(
            self.context,
            payload,
            action.generation,
        )
        expected = _host_expected(
            self.context,
            payload,
            generation=action.generation,
            profile_uuid=profile_uuid,
            inbound_uuid=inbound_uuid,
        )
        matches = _host_projection(host) == expected
        owned = binding is not None or self.context.was_managed(action.resource.logical_id, host.uuid)
        if host.uuid and (matches or owned):
            self.context.workloads.record_host_uuid(
                workload_id,
                action.generation,
                action.resource.logical_id,
                host.uuid,
            )
        return _observed(action, host.uuid, matches, _host_projection(host))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, HostPayload)
        profile_uuid, inbound_uuid, _ = _resolved_host_dependencies(
            self.context,
            payload,
            action.generation,
        )
        expected = _host_expected(
            self.context,
            payload,
            generation=action.generation,
            profile_uuid=profile_uuid,
            inbound_uuid=inbound_uuid,
        )
        existing = _bound_or_named_host(self.context, payload, binding)
        kwargs = {
            "remark": payload.remark,
            "address": expected["address"],
            "port": payload.public_port,
            "config_profile_uuid": profile_uuid,
            "inbound_uuid": inbound_uuid,
            "sni": payload.sni,
            "host_header": payload.host,
            "path": payload.path,
            "alpn": payload.alpn or None,
            "fingerprint": payload.fingerprint or None,
            "security_layer": payload.security_layer,
            "is_disabled": not payload.advertised,
            "tags": payload.tags,
            "is_hidden": payload.is_hidden,
            "xray_json_template_uuid": expected["xray_json_template_uuid"],
            "exclude_from_subscription_types": payload.exclude_from_subscription_types,
        }
        if existing is None:
            host = _mutation(
                lambda: self.context.panel.create_host(**kwargs),
                f"create Host {payload.remark}",
            )
        else:
            if binding is None and not self.context.was_managed(action.resource.logical_id, existing.uuid):
                raise ResourceReconcileError(
                    f"Unmanaged Remnawave Host collides with owned remark {payload.remark!r}.",
                    hint="Rename the unmanaged Host or explicitly recover its Meridian binding.",
                    category="user",
                )
            host = _mutation(
                lambda: self.context.panel.update_host(existing.uuid, **kwargs),
                f"update Host {payload.remark}",
            )
        return ResourceApplyReceipt(remote_id=host.uuid)


class InternalSquadDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, InternalSquadPayload)
        squad = _bound_or_named_squad(self.context, payload, binding)
        if squad is None:
            return ResourceObservation(exists=False)
        inbound_uuids = _resolved_ids(self.context, payload.inbound_refs, action.generation)
        matches = squad.name == payload.name and sorted(squad.inbound_uuids) == sorted(inbound_uuids)
        return _observed(action, squad.uuid, matches, _squad_projection(squad))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, InternalSquadPayload)
        inbound_uuids = _resolved_ids(self.context, payload.inbound_refs, action.generation)
        existing = _bound_or_named_squad(self.context, payload, binding)
        if existing is None:
            squad = _mutation(
                lambda: self.context.panel.create_internal_squad(payload.name, inbound_uuids),
                f"create Internal Squad {payload.name}",
            )
        else:
            if binding is None and not self.context.was_managed(action.resource.logical_id, existing.uuid):
                raise ResourceReconcileError(
                    f"Unmanaged Internal Squad collides with owned name {payload.name!r}.",
                    hint="Rename the unmanaged Squad or explicitly recover its Meridian binding.",
                    category="user",
                )
            squad = _mutation(
                lambda: self.context.panel.update_internal_squad(
                    existing.uuid,
                    name=payload.name,
                    inbound_uuids=inbound_uuids,
                ),
                f"update Internal Squad {payload.name}",
            )
        return ResourceApplyReceipt(remote_id=squad.uuid)


def _payload(action: ResourceAction, expected_type: type[PayloadT]) -> PayloadT:
    payload = action.resource.payload
    if not isinstance(payload, expected_type):
        raise ResourceReconcileError(f"Driver received {payload.kind}, expected {expected_type.__name__}.")
    return payload


def _profile_server_ref(context: RemnawaveDriverContext, profile: ConfigProfilePayload) -> str:
    bindings = [
        resource.payload
        for resource in context.plan.resources
        if isinstance(resource.payload, NodeBindingPayload)
        and resource.payload.profile_ref == f"profile:{profile.workload_id}"
    ]
    if len(bindings) != 1:
        raise ResourceReconcileError(f"Profile {profile.workload_id!r} must have exactly one reviewed Node binding.")
    return bindings[0].server_ref


def _profile_ref_for_workload(context: RemnawaveDriverContext, workload_id: str) -> str:
    logical_id = f"profile:{workload_id}"
    context.resource_payload(logical_id, ConfigProfilePayload)
    return logical_id


def _resolved_ids(
    context: RemnawaveDriverContext,
    logical_ids: list[str],
    generation: int,
) -> list[str]:
    values = [context.remote_id(logical_id, generation) for logical_id in logical_ids]
    missing = [logical_id for logical_id, remote_id in zip(logical_ids, values, strict=True) if not remote_id]
    if missing:
        raise ResourceReconcileError(f"Remote identities are missing for reviewed dependencies: {', '.join(missing)}.")
    return values


def _resolved_node_dependencies(
    context: RemnawaveDriverContext,
    payload: NodeBindingPayload,
    generation: int,
) -> tuple[str, list[str]]:
    profile_uuid = context.remote_id(payload.profile_ref, generation)
    if not profile_uuid:
        profile = context.workloads.require(payload.workload_ref, generation)
        profile_uuid = profile.config_profile_uuid
    if not profile_uuid:
        raise ResourceReconcileError(f"Profile UUID is missing for {payload.workload_ref}.")
    return profile_uuid, _resolved_ids(context, payload.inbound_refs, generation)


def _node_api_port(
    context: RemnawaveDriverContext,
    payload: NodeBindingPayload,
) -> int:
    runtimes = [
        resource.payload
        for resource in context.plan.resources
        if isinstance(resource.payload, NodeRuntimePayload)
        and resource.payload.binding_ref == f"binding:{payload.workload_ref}"
    ]
    if len(runtimes) != 1:
        raise ResourceReconcileError(f"Node binding {payload.workload_ref!r} must have exactly one reviewed runtime.")
    return runtimes[0].api_port


def _resolved_host_dependencies(
    context: RemnawaveDriverContext,
    payload: HostPayload,
    generation: int,
) -> tuple[str, str, str]:
    inbound = context.resource_payload(payload.inbound_ref, InboundPayload)
    workload = context.workloads.require(inbound.workload_ref, generation)
    profile_uuid = workload.config_profile_uuid
    inbound_uuid = context.remote_id(payload.inbound_ref, generation)
    if not inbound_uuid:
        inbound_uuid = workload.inbound_uuids.get(payload.inbound_ref, "")
    if not profile_uuid or not inbound_uuid:
        raise ResourceReconcileError(f"Profile-scoped Inbound identity is missing for Host {payload.remark!r}.")
    return profile_uuid, inbound_uuid, inbound.workload_ref


def _bound_or_named_profile(
    context: RemnawaveDriverContext,
    payload: ConfigProfilePayload,
    binding: ManagedResourceBinding | None,
) -> ConfigProfile | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_config_profile(binding.remote_id)
    workload = context.workloads.find(payload.workload_id, _generation_from_workload(context, payload.workload_id))
    if workload is not None and workload.config_profile_uuid:
        profile = context.panel.get_config_profile(workload.config_profile_uuid)
        if profile is not None:
            return profile
    matches = [profile for profile in context.panel.list_config_profiles() if profile.name == payload.name]
    return _one_or_none(matches, f"Profiles named {payload.name!r}")


def _bound_or_named_node(
    context: RemnawaveDriverContext,
    payload: NodeBindingPayload,
    binding: ManagedResourceBinding | None,
) -> Node | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_node(binding.remote_id)
    workload = context.workloads.find(
        payload.workload_ref,
        _generation_from_workload(context, payload.workload_ref),
    )
    if workload is not None:
        node_uuid = workload.node_uuids.get(payload.server_ref, "")
        if node_uuid:
            node = context.panel.get_node(node_uuid)
            if node is not None:
                return node
    matches = [node for node in context.panel.list_nodes() if node.name == payload.name]
    return _one_or_none(matches, f"Nodes named {payload.name!r}")


def _bound_or_named_host(
    context: RemnawaveDriverContext,
    payload: HostPayload,
    binding: ManagedResourceBinding | None,
) -> Host | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_host(binding.remote_id)
    matches = [host for host in context.panel.list_hosts() if host.remark == payload.remark]
    return _one_or_none(matches, f"Hosts with remark {payload.remark!r}")


def _bound_or_named_squad(
    context: RemnawaveDriverContext,
    payload: InternalSquadPayload,
    binding: ManagedResourceBinding | None,
) -> InternalSquad | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_internal_squad(binding.remote_id)
    matches = [squad for squad in context.panel.list_internal_squads() if squad.name == payload.name]
    return _one_or_none(matches, f"Internal Squads named {payload.name!r}")


def _generation_from_workload(context: RemnawaveDriverContext, workload_id: str) -> int:
    generations = [workload.generation for workload in context.cluster.workloads if workload.id == workload_id]
    return max(generations, default=0)


def _one_or_none(values: list[ValueT], description: str) -> ValueT | None:
    if len(values) > 1:
        raise ResourceReconcileError(f"Remnawave contains duplicate {description}.")
    return values[0] if values else None


def _observed(
    action: ResourceAction,
    remote_id: str,
    matches: bool,
    projection: dict[str, Any],
) -> ResourceObservation:
    satisfied = sorted(
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind == "exists"
    )
    return ResourceObservation(
        exists=True,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=satisfied,
    )


def _profile_projection(profile: ConfigProfile) -> dict[str, Any]:
    return {"name": profile.name, "config": profile.config}


def _inbound_projection(inbound: Inbound) -> dict[str, Any]:
    return {
        "tag": inbound.tag,
        "type": inbound.type,
        "network": inbound.network,
        "security": inbound.security,
        "profile_uuid": inbound.profile_uuid,
        "port": inbound.port,
    }


def _inbound_matches(inbound: Inbound, payload: InboundPayload, profile_uuid: str) -> bool:
    expected = {
        "reality": ("vless", "tcp", "reality"),
        "xhttp": ("vless", "xhttp", "none"),
        "wss": ("vless", "ws", "none"),
        "hysteria2": ("hysteria", "hysteria", "tls"),
    }[payload.protocol]
    return (
        inbound.tag == payload.tag
        and (inbound.type, inbound.network, inbound.security) == expected
        and inbound.port == payload.listen_port
        and inbound.profile_uuid in {"", profile_uuid}
    )


def _node_projection(node: Node) -> dict[str, Any]:
    return {
        "name": node.name,
        "address": node.address,
        "port": node.port,
        "is_disabled": node.is_disabled,
        "profile_uuid": node.active_config_profile_uuid,
        "inbound_uuids": sorted(node.active_inbound_uuids),
    }


def _host_expected(
    context: RemnawaveDriverContext,
    payload: HostPayload,
    *,
    generation: int,
    profile_uuid: str,
    inbound_uuid: str,
) -> dict[str, Any]:
    template_uuid = ""
    if payload.xray_json_template_ref:
        template_uuid = context.remote_id(
            payload.xray_json_template_ref,
            generation,
        )
        if not template_uuid:
            raise ResourceReconcileError(f"Template identity is missing for Host {payload.remark!r}.")
    return {
        "remark": payload.remark,
        "address": payload.address or context.address(payload.address_server_ref),
        "port": payload.public_port,
        "config_profile_uuid": profile_uuid,
        "inbound_uuid": inbound_uuid,
        "sni": payload.sni,
        "host": payload.host,
        "path": payload.path,
        "alpn": payload.alpn,
        "fingerprint": payload.fingerprint,
        "security_layer": payload.security_layer,
        "is_disabled": not payload.advertised,
        "tags": sorted(payload.tags),
        "is_hidden": payload.is_hidden,
        "xray_json_template_uuid": template_uuid,
        "exclude_from_subscription_types": sorted(payload.exclude_from_subscription_types),
    }


def _host_projection(host: Host) -> dict[str, Any]:
    return {
        "remark": host.remark,
        "address": host.address,
        "port": host.port,
        "config_profile_uuid": host.config_profile_uuid,
        "inbound_uuid": host.inbound_uuid,
        "sni": host.sni,
        "host": host.host,
        "path": host.path,
        "alpn": host.alpn,
        "fingerprint": host.fingerprint,
        "security_layer": host.security_layer,
        "is_disabled": host.is_disabled,
        "tags": sorted(host.tags),
        "is_hidden": host.is_hidden,
        "xray_json_template_uuid": host.xray_json_template_uuid,
        "exclude_from_subscription_types": sorted(host.exclude_from_subscription_types),
    }


def _squad_projection(squad: InternalSquad) -> dict[str, Any]:
    return {
        "name": squad.name,
        "inbound_uuids": sorted(squad.inbound_uuids),
    }


def _mutation(call: Callable[[], ValueT], description: str) -> ValueT:
    try:
        return call()
    except RemnawaveNetworkError as exc:
        raise UnknownResourceOutcome(f"Network outcome is unknown while attempting to {description}: {exc}") from exc
