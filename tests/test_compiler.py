"""Pure finite topology compiler behavior."""

from __future__ import annotations

import re
from collections import Counter

import pytest
from pydantic import ValidationError

from meridian.compiler import TopologyCompileError, compile_topology
from meridian.compiler.models import (
    COMPILER_VERSION,
    AccessUserPayload,
    CertificatePayload,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    DeploymentContract,
    DeploymentTarget,
    EgressPoolPayload,
    ExternalSquadPayload,
    FirewallRulePayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NginxArtifactPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
    RealmHopPayload,
    ResourcePlan,
    RouteRulePayload,
    ServerBaselinePayload,
    ServiceUserPayload,
    SubscriptionSettingsPayload,
    SubscriptionTemplatePayload,
)
from meridian.compiler.names import (
    REMNAWAVE_DISPLAY_NAME_PATTERN,
    REMNAWAVE_SHORT_NAME_MAX_LENGTH,
    REMNAWAVE_TEMPLATE_NAME_MAX_LENGTH,
    REMNAWAVE_USERNAME_MAX_LENGTH,
    REMNAWAVE_USERNAME_PATTERN,
    internal_squad_name,
    node_name,
    profile_name,
    service_username,
    subscription_template_name,
)
from meridian.compiler.routing import edge_outbound_tag
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
                strategy="least_ping",
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


def _deployment_contract() -> DeploymentContract:
    return DeploymentContract(
        server_targets={
            "srv-control": DeploymentTarget(host="198.51.100.10", user="root", port=22),
            "srv-exit-a": DeploymentTarget(host="198.51.100.11", user="ubuntu", port=2222),
            "srv-exit-b": DeploymentTarget(host="198.51.100.12", user="root", port=22),
            "srv-gateway": DeploymentTarget(host="198.51.100.13", user="root", port=22),
            "srv-relay-core": DeploymentTarget(host="198.51.100.14", user="root", port=22),
            "srv-relay-edge": DeploymentTarget(host="198.51.100.15", user="root", port=22),
        },
        runtime_pins={
            "meridian_version": "4.1.0",
            "realm_version": "2.9.3",
            "remnawave_backend_image": "remnawave/backend:2.8.0",
            "remnawave_node_image": "remnawave/node:2.8.0",
            "remnawave_subscription_page_image": "remnawave/subscription-page:7.2.6",
        },
        renderer_contract="meridian.renderers/v1@sha256:" + "a" * 64,
    )


class TestPureCompiler:
    def test_same_intent_produces_identical_reviewed_plan(self) -> None:
        first = compile_topology(_intent())
        second = compile_topology(_intent())

        assert first == second
        assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
        assert len(first.intent_hash) == 64
        assert len(first.plan_hash) == 64

    def test_default_deployment_contract_is_explicit_empty_and_deterministic(self) -> None:
        implicit = compile_topology(_intent())
        explicit = compile_topology(_intent(), deployment_contract=DeploymentContract())

        assert implicit == explicit
        assert implicit.compiler_version == COMPILER_VERSION
        assert implicit.deployment_contract == DeploymentContract()

    def test_deployment_contract_is_inspectable_and_order_independent(self) -> None:
        contract = _deployment_contract()
        reordered = DeploymentContract(
            server_targets=dict(reversed(contract.server_targets.items())),
            runtime_pins=dict(reversed(contract.runtime_pins.items())),
            renderer_contract=contract.renderer_contract,
        )

        plan = compile_topology(_intent(), deployment_contract=contract)
        reordered_plan = compile_topology(_intent(), deployment_contract=reordered)

        assert plan.deployment_contract == contract
        assert plan.plan_hash == reordered_plan.plan_hash
        assert plan.model_dump_json(by_alias=True) == reordered_plan.model_dump_json(by_alias=True)

    def test_every_deployment_contract_component_changes_plan_hash(self) -> None:
        contract = _deployment_contract()
        original = compile_topology(_intent(), deployment_contract=contract)

        def targets_with(**changes: object) -> dict[str, DeploymentTarget]:
            targets = dict(contract.server_targets)
            target = targets["srv-control"].model_dump(mode="python")
            target.update(changes)
            targets["srv-control"] = DeploymentTarget.model_validate(target)
            return targets

        variants = {
            "host": contract.model_copy(update={"server_targets": targets_with(host="198.51.100.20")}),
            "ssh user": contract.model_copy(update={"server_targets": targets_with(user="admin")}),
            "ssh port": contract.model_copy(update={"server_targets": targets_with(port=2200)}),
            "runtime image": contract.model_copy(
                update={
                    "runtime_pins": {
                        **contract.runtime_pins,
                        "remnawave_node_image": "remnawave/node:2.8.1",
                    }
                }
            ),
            "runtime version": contract.model_copy(
                update={"runtime_pins": {**contract.runtime_pins, "realm_version": "2.9.4"}}
            ),
            "renderer contract": contract.model_copy(
                update={"renderer_contract": "meridian.renderers/v2@sha256:" + "b" * 64}
            ),
        }

        for component, changed_contract in variants.items():
            changed = compile_topology(_intent(), deployment_contract=changed_contract)
            assert changed.plan_hash != original.plan_hash, component

    def test_semantically_unordered_exit_input_is_normalized(self) -> None:
        first = compile_topology(_intent())
        reordered = compile_topology(_intent(reverse_exits=True))

        assert reordered.intent_hash == first.intent_hash
        assert reordered.plan_hash == first.plan_hash
        assert reordered.resources == first.resources

    def test_generated_remnawave_names_respect_pinned_wire_contracts(self) -> None:
        plan = compile_topology(_intent())
        short_names = [
            resource.payload.name
            for resource in plan.resources
            if isinstance(
                resource.payload,
                ConfigProfilePayload | NodeBindingPayload | InternalSquadPayload | ExternalSquadPayload,
            )
        ]
        template_names = [
            resource.payload.name
            for resource in plan.resources
            if isinstance(resource.payload, SubscriptionTemplatePayload)
        ]
        usernames = [
            resource.payload.username
            for resource in plan.resources
            if isinstance(resource.payload, AccessUserPayload | ServiceUserPayload)
        ]

        assert short_names
        assert all(2 <= len(name) <= REMNAWAVE_SHORT_NAME_MAX_LENGTH for name in short_names)
        assert all(re.fullmatch(REMNAWAVE_DISPLAY_NAME_PATTERN, name) for name in short_names)
        assert all("/" not in name for name in short_names)
        assert all(2 <= len(name) <= REMNAWAVE_TEMPLATE_NAME_MAX_LENGTH for name in template_names)
        assert all(re.fullmatch(REMNAWAVE_DISPLAY_NAME_PATTERN, name) for name in template_names)
        assert all(3 <= len(name) <= REMNAWAVE_USERNAME_MAX_LENGTH for name in usernames)
        assert all(re.fullmatch(REMNAWAVE_USERNAME_PATTERN, name) for name in usernames)

    def test_truncated_generated_names_keep_a_stable_collision_suffix(self) -> None:
        first_id = "exit-" + "a" * 41 + "x"
        second_id = "exit-" + "a" * 41 + "y"

        first_profile = profile_name(first_id)
        second_profile = profile_name(second_id)

        assert first_profile == profile_name(first_id)
        assert first_profile == node_name(first_id)
        assert first_profile != second_profile
        assert len(first_profile) <= REMNAWAVE_SHORT_NAME_MAX_LENGTH
        assert internal_squad_name(first_id, "Shared") != internal_squad_name(second_id, "Shared")
        assert internal_squad_name(first_id, "Meridian " + "A" * 80) != internal_squad_name(
            second_id,
            "Meridian " + "A" * 80,
        )
        assert service_username(first_id) != service_username(second_id)
        assert len(service_username(first_id)) <= REMNAWAVE_USERNAME_MAX_LENGTH
        assert "/" not in subscription_template_name(first_id, "XRAY_JSON")

    def test_plan_uses_only_finite_v4_resource_kinds(self) -> None:
        plan = compile_topology(_intent())
        kinds = {resource.payload.kind for resource in plan.resources}

        assert {
            "server_baseline",
            "control_plane_runtime",
            "config_profile",
            "inbound",
            "node_binding",
            "node_runtime",
            "host",
            "internal_squad",
            "external_squad",
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

    def test_every_server_mutation_depends_on_one_server_baseline(self) -> None:
        plan = compile_topology(_intent())
        baselines = {
            resource.payload.server_ref: resource
            for resource in plan.resources
            if isinstance(resource.payload, ServerBaselinePayload)
        }
        assert set(baselines) == {
            "srv-control",
            "srv-exit-a",
            "srv-exit-b",
            "srv-gateway",
            "srv-relay-core",
            "srv-relay-edge",
        }
        assert baselines["srv-relay-core"].payload.install_docker is False
        assert baselines["srv-exit-a"].payload.install_docker is True

        server_mutations = (
            CertificatePayload,
            ControlPlaneRuntimePayload,
            FirewallRulePayload,
            NginxArtifactPayload,
            NodeRuntimePayload,
            RealmHopPayload,
        )
        for resource in plan.resources:
            if isinstance(resource.payload, server_mutations):
                assert baselines[resource.payload.server_ref].logical_id in resource.dependencies

    def test_every_exit_certificate_waits_for_its_reviewed_http_challenge_rule(self) -> None:
        plan = compile_topology(_intent())
        http_rules = {
            resource.payload.server_ref: resource.logical_id
            for resource in plan.resources
            if isinstance(resource.payload, FirewallRulePayload)
            and resource.payload.transport == "tcp"
            and resource.payload.port == 80
        }

        certificates = [resource for resource in plan.resources if isinstance(resource.payload, CertificatePayload)]

        assert certificates
        assert all(http_rules[resource.payload.server_ref] in resource.dependencies for resource in certificates)

    def test_control_and_exit_share_one_complete_port_443_stream_owner(self) -> None:
        intent = SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-shared"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-shared",
                    paths=[_reality("exit-a-reality", "www.microsoft.com")],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        )

        plan = compile_topology(intent)
        streams = [
            resource.payload
            for resource in plan.resources
            if isinstance(resource.payload, NginxArtifactPayload)
            and resource.payload.server_ref == "srv-shared"
            and resource.payload.listener_port == 443
            and resource.payload.layer == "stream"
        ]

        assert len(streams) == 1
        panel_route = next(route for route in streams[0].routes if "" in route.server_names)
        assert panel_route.backend_server_ref == "srv-shared"
        assert panel_route.backend_port == 8443
        assert any(
            "www.microsoft.com" in route.server_names and route.backend_port != 8443 for route in streams[0].routes
        )

    def test_delivery_compiles_owned_templates_and_client_capability_truth(self) -> None:
        plan = compile_topology(_intent())
        templates = [
            resource.payload for resource in plan.resources if isinstance(resource.payload, SubscriptionTemplatePayload)
        ]
        external = next(
            resource.payload for resource in plan.resources if isinstance(resource.payload, ExternalSquadPayload)
        )
        settings = next(
            resource.payload for resource in plan.resources if isinstance(resource.payload, SubscriptionSettingsPayload)
        )

        assert {template.template_type for template in templates} == {
            "XRAY_JSON",
            "MIHOMO",
        }
        xray = next(template for template in templates if template.template_type == "XRAY_JSON")
        assert xray.template_json is not None
        assert xray.template_json["routing"]["balancers"][0]["fallbackTag"] == "BLOCK"
        assert xray.template_json["remnawave"]["injectHosts"][0]["selectFrom"] == "HIDDEN"
        assert xray.template_json["burstObservatory"]["pingConfig"]["destination"].startswith("https://")
        mihomo = next(template.template_yaml for template in templates if template.template_type == "MIHOMO")
        assert mihomo is not None
        assert "type: fallback" in mihomo
        assert mihomo.count("# LEAVE THIS LINE!") == 2
        hosts = [resource.payload for resource in plan.resources if isinstance(resource.payload, HostPayload)]
        direct_hosts = [host for host in hosts if not host.is_hidden and not host.xray_json_template_ref]
        hidden_edges = [host for host in hosts if host.is_hidden]
        virtual_hosts = [host for host in hosts if host.xray_json_template_ref]
        assert len(hidden_edges) == len(direct_hosts)
        assert len(virtual_hosts) == 1
        assert all("XRAY_JSON" in host.exclude_from_subscription_types for host in direct_hosts)
        assert all(host.exclude_from_subscription_types == ["MIHOMO", "XRAY_BASE64"] for host in hidden_edges)
        assert virtual_hosts[0].exclude_from_subscription_types == [
            "MIHOMO",
            "XRAY_BASE64",
        ]
        assert len(external.template_refs) == 2
        assert settings.profile_title == "Family VPN"

    def test_base64_delivery_publishes_alternatives_without_fake_failover_template(self) -> None:
        intent = _intent().model_copy(update={"delivery": DeliveryIntent(formats=["base64"])})
        plan = compile_topology(intent)

        assert not any(
            isinstance(resource.payload, (SubscriptionTemplatePayload, ExternalSquadPayload))
            for resource in plan.resources
        )
        users = [resource.payload for resource in plan.resources if isinstance(resource.payload, AccessUserPayload)]
        assert users
        assert all(user.external_squad_ref == "" for user in users)

    def test_resources_are_dependency_ordered_with_unique_tags_and_ports(self) -> None:
        plan = compile_topology(_intent())
        positions = {resource.logical_id: index for index, resource in enumerate(plan.resources)}

        for resource in plan.resources:
            assert resource.dependencies == sorted(set(resource.dependencies))
            assert all(positions[dependency] < positions[resource.logical_id] for dependency in resource.dependencies)

        inbounds = [resource.payload for resource in plan.resources if isinstance(resource.payload, InboundPayload)]
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

        assert set(profiles) == {"exit-a", "exit-b", "gateway-a"}
        assert [(item.protocol, item.purpose) for item in profiles["exit-a"].inbounds] == [
            ("reality", "client"),
            ("xhttp", "client"),
            ("reality", "bridge"),
        ]
        assert [(item.protocol, item.purpose) for item in profiles["exit-b"].inbounds] == [
            ("hysteria2", "client"),
            ("reality", "client"),
            ("wss", "client"),
            ("reality", "bridge"),
        ]
        assert profiles["exit-a"].outbound_tags == ["direct"]
        assert profiles["exit-b"].outbound_tags == ["warp"]
        assert profiles["gateway-a"].workload_kind == "routing_gateway"
        assert len(profiles["gateway-a"].service_outbounds) == 2
        assert profiles["gateway-a"].egress_balancers[0].fallback_tag == "block"
        assert set(bindings["exit-a"].inbound_refs) == set(profiles["exit-a"].inbound_refs)
        assert set(bindings["exit-b"].inbound_refs) == set(profiles["exit-b"].inbound_refs)
        assert bindings["exit-a"].profile_ref == "profile:exit-a"
        assert bindings["exit-b"].profile_ref == "profile:exit-b"
        assert runtimes["exit-a"].warp is False
        assert runtimes["exit-b"].warp is True
        assert runtimes["gateway-a"].warp is False

        exit_a_wire = profiles["exit-a"].model_dump_json()
        exit_b_wire = profiles["exit-b"].model_dump_json()
        assert "www.cloudflare.com" not in exit_a_wire
        assert "vpn-b.example.com" not in exit_a_wire
        assert "www.microsoft.com" not in exit_b_wire
        assert "vpn-a.example.com" not in exit_b_wire
        gateway_wire = profiles["gateway-a"].model_dump_json()
        assert "www.microsoft.com" in gateway_wire
        assert "private" not in gateway_wire

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
            "host:gateway-a:entry:direct",
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

        squad = resources["squad:access"].payload
        assert isinstance(squad, InternalSquadPayload)
        assert squad.inbound_refs == sorted(
            [
                *client_inbound_refs(plan, "exit-a"),
                *client_inbound_refs(plan, "exit-b"),
                *client_inbound_refs(plan, "gateway-a"),
            ]
        )

    def test_gateway_compiles_private_edges_ordered_rules_and_fail_closed_pool(self) -> None:
        plan = compile_topology(_intent())
        resources = {resource.logical_id: resource for resource in plan.resources}
        profile = resources["profile:gateway-a"].payload
        assert isinstance(profile, ConfigProfilePayload)

        assert [edge.target_workload_ref for edge in profile.service_outbounds] == [
            "exit-a",
            "exit-b",
        ]
        assert [edge.tag for edge in profile.service_outbounds] == [
            "meridian-edge-0dbff829e6d1bdd5",
            "meridian-edge-c123917cc6e24846",
        ]
        assert profile.egress_balancers[0].outbound_tags == [edge.tag for edge in profile.service_outbounds]
        assert all(edge.target_port >= 40000 for edge in profile.service_outbounds)
        assert [rule.route_id for rule in profile.routing_rules] == ["regional", "default"]
        assert profile.routing_rules[-1].target_type == "balancer"
        assert profile.routing_rules[-1].target_tag == "meridian-pool-gateway-a-primary"

        pool = resources["egress-pool:gateway-a:primary"].payload
        assert isinstance(pool, EgressPoolPayload)
        assert pool.fail_closed is True
        assert pool.balancer_tag == "meridian-pool-gateway-a-primary"
        route = resources["route:gateway-a:regional"].payload
        assert isinstance(route, RouteRulePayload)
        assert route.priority == 10
        users = [resource.payload for resource in plan.resources if isinstance(resource.payload, ServiceUserPayload)]
        assert {(user.gateway_ref, user.target_ref) for user in users} == {
            ("gateway-a", "exit-a"),
            ("gateway-a", "exit-b"),
        }

    def test_gateway_edge_tags_are_deterministic_fixed_width_and_prefix_free(self) -> None:
        first = edge_outbound_tag("gateway-a", "exit-a")
        second = edge_outbound_tag("gateway-a", "exit-a-backup")

        assert first == edge_outbound_tag("gateway-a", "exit-a")
        assert len(first) == len(second)
        assert not first.startswith(second)
        assert not second.startswith(first)

    def test_gateway_rejects_generated_service_outbound_tag_collisions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def colliding_tag(_gateway_ref: str, _exit_ref: str) -> str:
            return "meridian-edge-collision"

        monkeypatch.setattr("meridian.compiler.routing.edge_outbound_tag", colliding_tag)

        with pytest.raises(TopologyCompileError, match="colliding service Outbound tags"):
            compile_topology(_intent())

    def test_control_plane_resources_do_not_claim_runtime_postconditions(self) -> None:
        plan = compile_topology(_intent())
        for resource in plan.resources:
            if isinstance(resource.payload, InboundPayload | NodeBindingPayload | HostPayload):
                assert [condition.kind for condition in resource.postconditions] == ["exists"]

    def test_hosts_are_published_after_live_listener_dependencies(self) -> None:
        plan = compile_topology(_intent())
        resources = {resource.logical_id: resource for resource in plan.resources}
        host_resources = [resource for resource in plan.resources if isinstance(resource.payload, HostPayload)]

        assert host_resources
        for host in host_resources:
            assert host.payload.inbound_ref in host.dependencies
            assert (
                any(dependency.startswith(("nginx:", "realm:")) for dependency in host.dependencies)
                or host.payload.protocol == "hysteria2"
            )
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
            if isinstance(resource.payload, HostPayload)
            and resource.payload.owner_ref == "relay-a"
            and not resource.payload.is_hidden
            and not resource.payload.xray_json_template_ref
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
    def test_deployment_contract_must_cover_every_server_ref_exactly(self) -> None:
        contract = _deployment_contract()
        incomplete = contract.model_copy(
            update={
                "server_targets": {
                    ref: target for ref, target in contract.server_targets.items() if ref != "srv-relay-core"
                }
            }
        )

        with pytest.raises(TopologyCompileError, match=r"missing srv-relay-core"):
            compile_topology(_intent(), deployment_contract=incomplete)

        extra = contract.model_copy(
            update={
                "server_targets": {
                    **contract.server_targets,
                    "srv-unused": DeploymentTarget(host="198.51.100.99", user="root", port=22),
                }
            }
        )
        with pytest.raises(TopologyCompileError, match=r"unexpected srv-unused"):
            compile_topology(_intent(), deployment_contract=extra)

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
            path.model_copy(update={"listen_port": 10443}) if path.protocol == "hysteria2" else path
            for path in exit_b.paths
        ]
        intent = original.model_copy(update={"exits": [original.exits[0], exit_b.model_copy(update={"paths": paths})]})

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

    def test_deployment_contract_tampering_is_rejected(self) -> None:
        plan = compile_topology(_intent(), deployment_contract=_deployment_contract())
        payload = plan.model_dump(mode="json", by_alias=True)
        payload["deployment_contract"]["server_targets"]["srv-control"]["port"] = 2200

        with pytest.raises(ValidationError, match="plan hash does not match"):
            ResourcePlan.model_validate(payload)


def profiles_inbound_refs(plan: ResourcePlan, workload_id: str) -> list[str]:
    profile = next(
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload) and resource.payload.workload_id == workload_id
    )
    return profile.inbound_refs


def client_inbound_refs(plan: ResourcePlan, workload_id: str) -> list[str]:
    profile = next(
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload) and resource.payload.workload_id == workload_id
    )
    return [
        inbound_ref
        for inbound_ref, inbound in zip(
            profile.inbound_refs,
            profile.inbounds,
            strict=True,
        )
        if inbound.purpose == "client"
    ]
