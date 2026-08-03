"""Public target resolution and V4-aware verification context."""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired

import pytest

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig, RelayEntry, ResourceAllocation
from meridian.core.errors import LocalStateError
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.servers import ServerEntry, ServerRegistry
from meridian.verification import (
    VerificationEvidenceUnavailableError,
    VerificationTargetError,
    deployment_verification_context,
    resolve_deployment_verification_context,
    resolve_domain,
    resolve_verification_target,
)


def _registry(path: Path) -> ServerRegistry:
    registry = ServerRegistry(path)
    for entry in (
        ServerEntry(id="srv-control", host="198.51.100.10", name="control"),
        ServerEntry(id="srv-exit", host="198.51.100.11", name="exit"),
        ServerEntry(id="srv-relay", host="198.51.100.12", name="relay"),
        ServerEntry(id="srv-gateway", host="198.51.100.13", name="gateway"),
    ):
        registry.add(entry)
    return registry


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="reality-a",
                        protocol="reality",
                        reality_sni="www.example.com",
                    ),
                    ProtocolPathIntent(
                        id="xhttp-a",
                        protocol="xhttp",
                        tls_sni="tls.example.com",
                        host="edge.example.com",
                        path="transport-a",
                    ),
                    ProtocolPathIntent(
                        id="hysteria-a",
                        protocol="hysteria2",
                        tls_sni="edge.example.com",
                    ),
                ],
            )
        ],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-a",
                protocol_path_ref="reality-a",
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


def test_explicit_ip_does_not_read_a_corrupt_registry(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text("not-json", encoding="utf-8")

    target = resolve_verification_target(
        "198.51.100.20",
        "",
        ServerRegistry(path),
        allow_domain=True,
    )

    assert target.ip == "198.51.100.20"


def test_explicit_external_target_survives_unrelated_corrupt_cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_load(_cls: type[ClusterConfig]) -> ClusterConfig:
        raise LocalStateError("cluster is corrupt")

    monkeypatch.setattr(ClusterConfig, "load", classmethod(fail_load))

    cluster, context, check = resolve_deployment_verification_context(
        ServerRegistry(tmp_path / "servers.json"),
        "198.51.100.20",
        allow_external_fallback=True,
    )

    assert cluster.topology_intent is None
    assert cluster.panel.url == ""
    assert context.kind == "external"
    assert check is not None
    assert check.status == "skipped"
    assert check.findings[0].code == "LOCAL_CONTEXT_UNAVAILABLE"


def test_explicit_target_turns_v4_compile_failure_into_skipped_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster = ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-exit",
                    paths=[ProtocolPathIntent(id="reality", protocol="reality", reality_sni="www.example.com")],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        )
    )
    monkeypatch.setattr(
        "meridian.verification.compile_topology",
        lambda _intent: (_ for _ in ()).throw(ValueError("bad plan")),
    )

    _cluster_result, context, check = resolve_deployment_verification_context(
        _registry(tmp_path / "servers.json"),
        "198.51.100.11",
        cluster=cluster,
        allow_external_fallback=True,
    )

    assert context.kind == "external"
    assert check is not None
    assert check.status == "skipped"


def test_saved_server_name_is_resolved_before_dns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry(tmp_path / "servers.json")
    monkeypatch.setattr("meridian.verification.resolve_domain", lambda _name, **_kwargs: "203.0.113.77")

    target = resolve_verification_target("", "exit", registry, allow_domain=True)

    assert target.ip == "198.51.100.11"
    assert target.label == "exit (198.51.100.11)"


def test_domain_target_reports_resolver_unavailability_as_system_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("meridian.verification.resolve_domain", lambda _name, **_kwargs: None)

    with pytest.raises(VerificationEvidenceUnavailableError) as exc_info:
        resolve_verification_target(
            "slow.example.com",
            "",
            ServerRegistry(tmp_path / "servers.json"),
            allow_domain=True,
        )

    assert exc_info.value.exit_code == 3


def test_target_argument_and_server_flag_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(VerificationTargetError, match="either"):
        resolve_verification_target(
            "198.51.100.20",
            "exit",
            _registry(tmp_path / "servers.json"),
            allow_domain=True,
        )


def test_v4_context_matches_canonical_ipv6_spelling(tmp_path: Path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="2001:0DB8:0:0:0:0:0:10", name="control"))
    registry.add(ServerEntry(id="srv-exit", host="2001:0DB8:0:0:0:0:0:11", name="exit"))
    registry.add(ServerEntry(id="srv-relay", host="2001:0DB8:0:0:0:0:0:12", name="relay"))

    context = deployment_verification_context(
        ClusterConfig(topology_intent=_intent()),
        registry,
        "2001:db8::11",
    )

    assert context.kind == "v4"
    assert context.roles == ("exit:exit-a",)
    assert context.expected_egress_ip == "2001:db8::11"
    assert "2001:db8::11" in context.endpoint_addresses


def test_legacy_context_matches_canonical_ipv6_spelling(tmp_path: Path) -> None:
    context = deployment_verification_context(
        ClusterConfig(nodes=[NodeEntry(ip="2001:0DB8:0:0:0:0:0:11", sni="www.example.com")]),
        ServerRegistry(tmp_path / "servers.json"),
        "2001:db8::11",
    )

    assert context.kind == "legacy"
    assert context.expected_egress_ip == "2001:db8::11"


def test_v4_context_uses_persisted_allocations_and_related_relay(tmp_path: Path) -> None:
    cluster = ClusterConfig(
        topology_intent=_intent(),
        allocations={
            "inbound:exit-a:reality-a": ResourceAllocation(
                logical_id="inbound:exit-a:reality-a",
                server_ref="srv-exit",
                transport="tcp",
                port=39001,
            )
        },
    )

    context = deployment_verification_context(
        cluster,
        _registry(tmp_path / "servers.json"),
        "198.51.100.11",
    )

    assert context.kind == "v4"
    assert context.roles == ("exit:exit-a",)
    assert context.internal_ports["exit-a/reality"] == 39001
    assert context.reality_snis == ("www.example.com",)
    assert context.domains == ("edge.example.com",)
    assert any(
        route.kind == "managed" and route.tls_sni == "tls.example.com" and route.host_header == "edge.example.com"
        for route in context.https_routes
    )
    assert context.public_tcp_ports == (443,)
    assert context.public_tls_ports == (443,)
    assert context.public_udp_ports == (443,)
    assert context.expected_egress_ip == "198.51.100.11"
    assert any(name.startswith("nginx/") for name in context.internal_ports)
    assert set(context.endpoint_addresses) >= {
        "198.51.100.11",
        "198.51.100.12",
        "edge.example.com",
    }


def test_v4_relay_context_uses_compiled_listener(tmp_path: Path) -> None:
    context = deployment_verification_context(
        ClusterConfig(topology_intent=_intent()),
        _registry(tmp_path / "servers.json"),
        "198.51.100.12",
    )

    assert context.kind == "v4"
    assert context.roles == ("relay:relay-a:hop:0",)
    assert context.public_tcp_ports == (443,)
    assert context.public_tls_ports == (443,)
    assert context.internal_ports == {}


def test_v4_internal_relay_hop_is_not_projected_as_public(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-relay-internal", host="198.51.100.14", name="relay-internal"))
    relay = TransparentRelayIntent(
        id="relay-a",
        hop_server_refs=["srv-relay", "srv-relay-internal"],
        exit_ref="exit-a",
        protocol_path_ref="reality-a",
    )
    intent = _intent().model_copy(update={"transparent_relays": [relay]})

    context = deployment_verification_context(
        ClusterConfig(topology_intent=intent),
        registry,
        "198.51.100.14",
    )

    assert context.kind == "v4"
    assert context.roles == ("relay:relay-a:hop:1",)
    assert context.public_tcp_ports == ()
    assert context.public_tls_ports == ()
    assert context.internal_ports == {"realm/relay-a/hop/1": 443}


def test_hysteria_sni_is_not_projected_as_an_https_route(tmp_path: Path) -> None:
    intent = _intent()
    paths = list(intent.exits[0].paths)
    paths[2] = paths[2].model_copy(update={"tls_sni": "hy.example.com", "public_port": 8443})
    exits = [intent.exits[0].model_copy(update={"paths": paths})]
    intent = intent.model_copy(update={"exits": exits})

    context = deployment_verification_context(
        ClusterConfig(topology_intent=intent),
        _registry(tmp_path / "servers.json"),
        "198.51.100.11",
    )

    assert context.public_udp_ports == (8443,)
    assert "hy.example.com" not in context.domains
    assert all(route.tls_sni != "hy.example.com" for route in context.https_routes)


def test_hostname_less_control_plane_has_a_managed_ip_route(tmp_path: Path) -> None:
    context = deployment_verification_context(
        ClusterConfig(topology_intent=_intent()),
        _registry(tmp_path / "servers.json"),
        "198.51.100.10",
    )

    assert context.roles == ("control",)
    assert len(context.https_routes) == 1
    assert context.https_routes[0].kind == "managed"
    assert context.https_routes[0].panel is True
    assert context.https_routes[0].tls_sni == ""
    assert context.https_routes[0].host_header == ""


def test_colocated_control_marks_only_its_https_route_as_panel(tmp_path: Path) -> None:
    intent = _intent().model_copy(
        update={
            "control": ControlPlaneIntent(
                server_ref="srv-exit",
                public_hostname="panel.example.com",
            )
        }
    )
    context = deployment_verification_context(
        ClusterConfig(
            topology_intent=intent,
            panel=PanelConfig(secret_path="secret"),
        ),
        _registry(tmp_path / "servers.json"),
        "198.51.100.11",
    )

    panel_routes = [route for route in context.https_routes if route.panel]
    assert [(route.tls_sni, route.host_header) for route in panel_routes] == [
        ("panel.example.com", "panel.example.com")
    ]
    assert any(route.host_header == "edge.example.com" and not route.panel for route in context.https_routes)


def test_v4_bridge_listener_is_checked_as_internal_surface(tmp_path: Path) -> None:
    intent = _intent().model_copy(
        update={
            "routing_gateways": [
                RoutingGatewayIntent(
                    id="gateway-a",
                    server_ref="srv-gateway",
                    bridge_path_ref="reality-a",
                )
            ]
        }
    )
    context = deployment_verification_context(
        ClusterConfig(topology_intent=intent),
        _registry(tmp_path / "servers.json"),
        "198.51.100.11",
    )

    bridge_ports = {name: port for name, port in context.internal_ports.items() if name.startswith("bridge/")}
    assert len(bridge_ports) == 1
    assert next(iter(bridge_ports.values())) >= 40000


def test_domain_resolution_honors_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise TimeoutExpired("resolver", 0.001)

    monkeypatch.setattr("meridian.diagnostics.network.subprocess.run", timeout)
    result = resolve_domain("slow.example.com", timeout=0.001)

    assert result is None


def test_legacy_context_remains_supported(tmp_path: Path) -> None:
    cluster = ClusterConfig(
        nodes=[
            NodeEntry(
                ip="198.51.100.11",
                sni="www.example.com",
                domain="edge.example.com",
                hysteria2=True,
            )
        ]
    )

    context = deployment_verification_context(
        cluster,
        ServerRegistry(tmp_path / "missing.json"),
        "198.51.100.11",
    )

    assert context.kind == "legacy"
    assert context.public_udp_ports == (443,)
    assert context.reality_snis == ("www.example.com",)
    assert "xhttp" in context.internal_ports
    assert "reality" in context.internal_ports


def test_legacy_relay_projects_stale_xhttp_host_for_verification(tmp_path: Path) -> None:
    cluster = ClusterConfig(
        nodes=[NodeEntry(ip="198.51.100.11", sni="www.example.com")],
        relays=[
            RelayEntry(
                ip="198.51.100.12",
                exit_node_ip="198.51.100.11",
                sni="relay.test",
                host_uuids={
                    "reality": "550e8400-e29b-41d4-a716-446655440001",
                    "xhttp": "550e8400-e29b-41d4-a716-446655440002",
                },
            )
        ],
    )

    context = deployment_verification_context(
        cluster,
        ServerRegistry(tmp_path / "missing.json"),
        "198.51.100.12",
    )

    assert context.protocols == ("reality", "xhttp")
    assert any(route.kind == "managed" and route.tls_sni == "relay.test" for route in context.https_routes)
