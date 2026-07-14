"""Pure finite topology compiler behavior."""

from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from meridian.compiler import TopologyCompileError, compile_topology
from meridian.compiler.models import (
    HostPayload,
    InboundPayload,
    RealmHopPayload,
    ResourcePlan,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)


def _reality(path_id: str, sni: str) -> ProtocolPathIntent:
    return ProtocolPathIntent(id=path_id, protocol="reality", reality_sni=sni)


def _xhttp(path_id: str, hostname: str, path: str) -> ProtocolPathIntent:
    return ProtocolPathIntent(
        id=path_id,
        protocol="xhttp",
        tls_sni=hostname,
        host=hostname,
        path=path,
    )


def _intent(*, reverse_exits: bool = False) -> SetupIntent:
    exits = [
        ExitIntent(
            id="exit-a",
            server_ref="srv-exit-a",
            region="DE",
            paths=[
                _reality("reality-a", "www.microsoft.com"),
                _xhttp("xhttp-a", "vpn-a.example.com", "xhttp-a"),
            ],
        ),
        ExitIntent(
            id="exit-b",
            server_ref="srv-exit-b",
            region="NL",
            paths=[
                _reality("reality-b", "www.cloudflare.com"),
                ProtocolPathIntent(
                    id="wss-b",
                    protocol="wss",
                    tls_sni="vpn-b.example.com",
                    host="vpn-b.example.com",
                    path="ws-b",
                ),
                ProtocolPathIntent(
                    id="hy2-b",
                    protocol="hysteria2",
                    tls_sni="vpn-b.example.com",
                ),
            ],
            warp=True,
        ),
    ]
    if reverse_exits:
        exits.reverse()
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control", public_hostname="panel.example.com"),
        exits=exits,
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay-edge", "srv-relay-core"],
                exit_ref="exit-a",
                protocol_path_ref="reality-a",
            )
        ],
        routing_gateways=[
            RoutingGatewayIntent(
                id="gateway-a",
                server_ref="srv-gateway",
                bridge_path_ref="reality-a",
            )
        ],
        egress_pools=[
            EgressPoolIntent(
                id="primary",
                exit_refs=["exit-a", "exit-b"],
                strategy="priority",
            )
        ],
        routes=[
            OrderedRouteIntent(
                id="regional",
                priority=10,
                match="country",
                match_values=["RU"],
                target_ref="primary",
                source_gateway_ref="gateway-a",
            )
        ],
        default_egress_ref="primary",
        access=AccessIntent(users=["default", "family"]),
        delivery=DeliveryIntent(profile_title="Family VPN"),
    )


class TestPureCompiler:
    def test_same_intent_produces_identical_reviewed_plan(self) -> None:
        first = compile_topology(_intent())
        second = compile_topology(_intent())

        assert first == second
        assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
        assert len(first.intent_hash) == 64
        assert len(first.plan_hash) == 64

    def test_semantically_unordered_exit_input_is_normalized(self) -> None:
        first = compile_topology(_intent())
        reordered = compile_topology(_intent(reverse_exits=True))

        assert reordered.intent_hash == first.intent_hash
        assert reordered.plan_hash == first.plan_hash
        assert reordered.resources == first.resources

    def test_plan_uses_only_finite_v4_resource_kinds(self) -> None:
        plan = compile_topology(_intent())
        kinds = {resource.payload.kind for resource in plan.resources}

        assert {
            "control_plane_runtime",
            "config_profile",
            "inbound",
            "node_binding",
            "node_runtime",
            "host",
            "internal_squad",
            "access_user",
            "service_user",
            "subscription_template",
            "subscription_settings",
            "nginx_artifact",
            "realm_hop",
            "firewall_rule",
            "certificate",
            "routing_gateway",
            "egress_pool",
            "route_rule",
            "probe",
        } <= kinds

    def test_resources_are_dependency_ordered_with_unique_tags_and_ports(self) -> None:
        plan = compile_topology(_intent())
        positions = {resource.logical_id: index for index, resource in enumerate(plan.resources)}

        for resource in plan.resources:
            assert resource.dependencies == sorted(set(resource.dependencies))
            assert all(positions[dependency] < positions[resource.logical_id] for dependency in resource.dependencies)

        inbounds = [
            resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, InboundPayload)
        ]
        tags = [inbound.tag for inbound in inbounds]
        assert len(tags) == len(set(tags))
        listeners = [(inbound.workload_ref, inbound.listen_port) for inbound in inbounds]
        assert len(listeners) == len(set(listeners))

    def test_hosts_are_published_after_live_listener_dependencies(self) -> None:
        plan = compile_topology(_intent())
        resources = {resource.logical_id: resource for resource in plan.resources}
        host_resources = [
            resource
            for resource in plan.resources
            if isinstance(resource.payload, HostPayload)
        ]

        assert host_resources
        for host in host_resources:
            assert host.payload.inbound_ref in host.dependencies
            assert any(
                dependency.startswith(("nginx:", "realm:"))
                for dependency in host.dependencies
            ) or host.payload.protocol == "hysteria2"
            assert resources[host.payload.inbound_ref].payload.kind == "inbound"

    def test_realm_hops_execute_downstream_first_and_only_first_is_advertised(self) -> None:
        plan = compile_topology(_intent())
        hops = {
            resource.payload.hop_index: resource
            for resource in plan.resources
            if isinstance(resource.payload, RealmHopPayload)
        }

        assert set(hops) == {0, 1}
        assert hops[0].payload.advertised is True
        assert hops[1].payload.advertised is False
        assert hops[1].logical_id in hops[0].dependencies
        relay_hosts = [
            resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, HostPayload) and resource.payload.owner_ref == "relay-a"
        ]
        assert [host.address_server_ref for host in relay_hosts] == ["srv-relay-edge"]

    def test_changing_endpoint_changes_plan_hash(self) -> None:
        original = _intent()
        changed_exit = original.exits[0].model_copy(
            update={
                "paths": [
                    _reality("reality-a", "www.microsoft.com"),
                    _xhttp("xhttp-a", "vpn-a.example.com", "changed-path"),
                ]
            }
        )
        changed = original.model_copy(update={"exits": [changed_exit, original.exits[1]]})

        assert compile_topology(changed).plan_hash != compile_topology(original).plan_hash

    def test_plan_never_contains_secret_shaped_payload_fields(self) -> None:
        plan_schema = ResourcePlan.model_json_schema()
        names: list[str] = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    names.extend(str(name).lower() for name in properties)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(plan_schema)
        assert not Counter(names).keys() & {"password", "api_token", "private_key", "secret_key"}


class TestCompilerFailures:
    def test_same_sni_cannot_route_to_two_workloads_on_one_listener(self) -> None:
        intent = SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-shared",
                    paths=[_reality("reality-a", "www.microsoft.com")],
                ),
                ExitIntent(
                    id="exit-b",
                    server_ref="srv-shared",
                    paths=[_reality("reality-b", "www.microsoft.com")],
                ),
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        )

        with pytest.raises(TopologyCompileError, match="cannot route SNI"):
            compile_topology(intent)

    def test_reviewed_hash_tampering_is_rejected(self) -> None:
        plan = compile_topology(_intent())
        payload = plan.model_dump(mode="json", by_alias=True)
        payload["resources"][0]["desired_hash"] = "0" * 64

        with pytest.raises(ValidationError, match="desired hash does not match"):
            ResourcePlan.model_validate(payload)
