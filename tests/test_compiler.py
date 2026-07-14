"""Pure finite topology compiler behavior."""

from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from meridian.compiler import TopologyCompileError, compile_topology
from meridian.compiler.models import (
    ConfigProfilePayload,
    FirewallRulePayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
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
                reality_sni="relay-a.example.com",
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

    def test_two_exits_keep_profiles_paths_and_egress_isolated(self) -> None:
        plan = compile_topology(_intent())
        profiles = {
            resource.payload.workload_id: resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, ConfigProfilePayload)
        }
        bindings = {
            resource.payload.workload_ref: resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, NodeBindingPayload)
        }
        runtimes = {
            resource.payload.workload_ref: resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, NodeRuntimePayload)
        }

        assert set(profiles) == {"exit-a", "exit-b"}
        assert [item.protocol for item in profiles["exit-a"].inbounds] == ["reality", "xhttp"]
        assert [item.protocol for item in profiles["exit-b"].inbounds] == ["hysteria2", "reality", "wss"]
        assert profiles["exit-a"].outbound_tags == ["direct"]
        assert profiles["exit-b"].outbound_tags == ["warp"]
        assert set(bindings["exit-a"].inbound_refs) == set(profiles["exit-a"].inbound_refs)
        assert set(bindings["exit-b"].inbound_refs) == set(profiles["exit-b"].inbound_refs)
        assert bindings["exit-a"].profile_ref == "profile:exit-a"
        assert bindings["exit-b"].profile_ref == "profile:exit-b"
        assert runtimes["exit-a"].warp is False
        assert runtimes["exit-b"].warp is True

        exit_a_wire = profiles["exit-a"].model_dump_json()
        exit_b_wire = profiles["exit-b"].model_dump_json()
        assert "www.cloudflare.com" not in exit_a_wire
        assert "vpn-b.example.com" not in exit_a_wire
        assert "www.microsoft.com" not in exit_b_wire
        assert "vpn-a.example.com" not in exit_b_wire

    def test_two_exits_publish_exact_owned_hosts_and_shared_access_squad(self) -> None:
        plan = compile_topology(_intent())
        resources = {resource.logical_id: resource for resource in plan.resources}
        direct_hosts = {
            resource.logical_id: resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, HostPayload) and resource.logical_id.endswith(":direct")
        }

        assert set(direct_hosts) == {
            "host:exit-a:reality-a:direct",
            "host:exit-a:xhttp-a:direct",
            "host:exit-b:hy2-b:direct",
            "host:exit-b:reality-b:direct",
            "host:exit-b:wss-b:direct",
        }
        assert direct_hosts["host:exit-a:reality-a:direct"].fingerprint == "chrome"
        assert direct_hosts["host:exit-a:xhttp-a:direct"].path == "/xhttp-a"
        assert direct_hosts["host:exit-a:xhttp-a:direct"].address == "vpn-a.example.com"
        assert direct_hosts["host:exit-b:wss-b:direct"].path == "/ws-b"
        assert direct_hosts["host:exit-b:hy2-b:direct"].alpn == "h3"
        assert direct_hosts["host:exit-b:hy2-b:direct"].security_layer == "TLS"

        hysteria = resources["host:exit-b:hy2-b:direct"]
        assert any(
            isinstance(resources[dependency].payload, FirewallRulePayload)
            and resources[dependency].payload.transport == "udp"
            for dependency in hysteria.dependencies
        )
        assert any(dependency.startswith("certificate:exit-b:") for dependency in hysteria.dependencies)

        squad = next(
            resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, InternalSquadPayload)
        )
        assert squad.inbound_refs == sorted(
            [*profiles_inbound_refs(plan, "exit-a"), *profiles_inbound_refs(plan, "exit-b")]
        )

    def test_control_plane_resources_do_not_claim_runtime_postconditions(self) -> None:
        plan = compile_topology(_intent())
        for resource in plan.resources:
            if isinstance(resource.payload, InboundPayload | NodeBindingPayload | HostPayload):
                assert [condition.kind for condition in resource.postconditions] == ["exists"]

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

    def test_custom_relay_sni_is_accepted_before_its_host_is_published(self) -> None:
        plan = compile_topology(_intent())
        resources = {resource.logical_id: resource for resource in plan.resources}
        inbound = resources["inbound:exit-a:reality-a"]
        assert isinstance(inbound.payload, InboundPayload)
        assert inbound.payload.reality_sni == "www.microsoft.com"
        assert inbound.payload.reality_server_names == [
            "www.microsoft.com",
            "relay-a.example.com",
        ]

        stream = next(
            resource
            for resource in plan.resources
            if resource.logical_id.startswith("nginx:")
            and any(
                getattr(route, "server_names", []) == ["relay-a.example.com"]
                for route in getattr(resource.payload, "routes", [])
            )
        )
        relay_host = resources["host:relay-a:reality-a"]
        assert relay_host.payload.sni == "relay-a.example.com"
        assert stream.logical_id in resources["realm:relay-a:1"].dependencies
        assert "realm:relay-a:0" in relay_host.dependencies

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
    def test_two_exit_workloads_cannot_share_one_node_runtime(self) -> None:
        original = _intent()
        shared = original.exits[1].model_copy(update={"server_ref": original.exits[0].server_ref})
        intent = original.model_copy(update={"exits": [original.exits[0], shared]})

        with pytest.raises(TopologyCompileError, match="own server"):
            compile_topology(intent)

    def test_hysteria_cannot_advertise_an_unforwarded_udp_port(self) -> None:
        original = _intent()
        exit_b = original.exits[1]
        paths = [
            path.model_copy(update={"listen_port": 10443})
            if path.protocol == "hysteria2"
            else path
            for path in exit_b.paths
        ]
        intent = original.model_copy(
            update={"exits": [original.exits[0], exit_b.model_copy(update={"paths": paths})]}
        )

        with pytest.raises(TopologyCompileError, match="public UDP port"):
            compile_topology(intent)

    def test_same_sni_cannot_route_to_two_backends_on_one_listener(self) -> None:
        intent = SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-shared",
                    paths=[
                        _reality("reality-a", "www.microsoft.com"),
                        _xhttp("xhttp-a", "www.microsoft.com", "xhttp-a"),
                    ],
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


def profiles_inbound_refs(plan: ResourcePlan, workload_id: str) -> list[str]:
    profile = next(
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload)
        and resource.payload.workload_id == workload_id
    )
    return profile.inbound_refs
