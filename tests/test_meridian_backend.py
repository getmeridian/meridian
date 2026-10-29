"""Meridian control-service backend: the skeleton plan shape, its refusals, and the hidden deploy switch."""

from __future__ import annotations

import pytest

from meridian.compiler import compile_topology
from meridian.compiler.deployment import deployment_server_refs
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import (
    AgentRuntimePayload,
    CertificatePayload,
    ClientEndpointPayload,
    ControlServicePayload,
    NginxArtifactPayload,
    NodeWorkloadPayload,
)
from meridian.core.deploy import DeployRequest
from meridian.core.errors import LocalStateError
from meridian.core.topology import EgressOverrideIntent, ExitIntent, RoutingGatewayIntent, TransparentRelayIntent
from meridian.setup.default_paths import default_exit_paths
from meridian.setup.single_server import request_intent, single_server_intent
from tests.support.topology import GATEWAY_SERVERS, gateway_fleet_intent

ADDRESS = "198.51.100.10"
DOMAIN = "vpn.example.com"
PANEL = "panel.example.com"


def _intent(**changes: object):
    intent = single_server_intent(None, server_ref="srv-a", address=ADDRESS, control="meridian")
    return intent.model_copy(update=changes) if changes else intent


def _overrides(*actions: str) -> list[EgressOverrideIntent]:
    return [
        EgressOverrideIntent(id=f"o-{action}", match="domain", values=[f"{action}.example.org"], action=action)
        for action in actions
    ]


def _domain_intent(*, sni: str = "", overrides: tuple[str, ...] = ()):
    """`deploy --domain` (XHTTP and WSS on the domain), with self-steal when *sni* is given."""
    intent = single_server_intent(
        None, server_ref="srv-a", address=ADDRESS, control="meridian", domain=DOMAIN, sni=sni, self_steal=bool(sni)
    )
    exit_ = intent.exits[0].model_copy(update={"egress_overrides": _overrides(*overrides)})
    return intent.model_copy(update={"exits": [exit_]})


def test_meridian_plan_replaces_every_panel_kind() -> None:
    plan = compile_topology(_intent())
    kinds = {item.payload.kind for item in plan.resources}

    assert {"control_service", "agent_runtime", "node_workload", "control_user", "service_settings"} <= kinds
    assert not kinds & {"control_plane_runtime", "profile", "inbound", "node", "host", "access_user", "squad"}
    endpoints = {
        item.logical_id: item.payload.fingerprint_copy  # type: ignore[union-attr]
        for item in plan.resources
        if item.payload.kind == "client_endpoint"
    }
    assert endpoints == {
        "endpoint:main:main-hysteria2:direct": False,
        "endpoint:main:main-reality:direct": False,
        "endpoint:main:main-reality:direct:firefox": True,
        "endpoint:main:main-xhttp:direct": False,
        "endpoint:main:main-xhttp:direct:firefox": True,
    }


def test_meridian_plan_keeps_certificates_independent_of_the_workload() -> None:
    """The workload needs the certificate; the reverse would deadlock the first apply."""
    plan = compile_topology(_intent())
    by_id = {item.logical_id: item for item in plan.resources}
    certificate = next(item for item in plan.resources if isinstance(item.payload, CertificatePayload))
    workload = next(item for item in plan.resources if isinstance(item.payload, NodeWorkloadPayload))

    assert workload.logical_id not in certificate.dependencies
    assert certificate.logical_id in by_id[workload.logical_id].dependencies


def test_meridian_baseline_skips_docker() -> None:
    plan = compile_topology(_intent())
    baseline = next(item for item in plan.resources if item.payload.kind == "server_baseline")

    assert baseline.payload.install_docker is False  # type: ignore[union-attr]


def _relay() -> TransparentRelayIntent:
    return TransparentRelayIntent(id="r1", hop_server_refs=["srv-r"], exit_ref="main", protocol_path_ref="main-reality")


def _second_exit() -> ExitIntent:
    return ExitIntent(id="de", server_ref="srv-b", paths=default_exit_paths("de", address="198.51.100.20"))


def test_meridian_backend_refuses_client_ip_forwarding() -> None:
    """It changes the PROXY protocol chain in front of every workload and needs its own review."""
    with pytest.raises(TopologyCompileError, match="client IP forwarding"):
        compile_topology(_intent(forward_client_ips=True))


def _warp_exit(*, default: bool, overrides: tuple[str, ...] = ()) -> ExitIntent:
    return _intent().exits[0].model_copy(update={"warp": default, "egress_overrides": _overrides(*overrides)})


@pytest.mark.parametrize(
    ("exit_", "outbound_tags", "rules"),
    [
        (_warp_exit(default=True), ["warp"], []),
        (_warp_exit(default=False, overrides=("warp",)), ["direct", "warp"], ["warp"]),
        (_warp_exit(default=True, overrides=("direct", "block")), ["warp"], ["direct", "block"]),
    ],
    ids=["warp-default", "warp-override", "warp-default-with-overrides"],
)
def test_warp_installs_with_the_agent_and_routes_in_the_workload(
    exit_: ExitIntent, outbound_tags: list[str], rules: list[str]
) -> None:
    plan = compile_topology(_intent(exits=[exit_]))
    agent = next(item.payload for item in plan.resources if isinstance(item.payload, AgentRuntimePayload))
    workload = next(item.payload for item in plan.resources if isinstance(item.payload, NodeWorkloadPayload))

    assert agent.warp is True
    assert workload.profile.outbound_tags == outbound_tags
    assert [rule.target_tag for rule in workload.profile.routing_rules] == rules


def test_an_agent_without_warp_keeps_its_reviewed_hash() -> None:
    """Existing fleets must not re-apply because the payload grew a field."""
    plan = compile_topology(_intent())
    agent = next(item.payload for item in plan.resources if isinstance(item.payload, AgentRuntimePayload))

    assert "warp" not in agent.model_dump(mode="json")


@pytest.mark.parametrize(
    ("sni", "decoy_hostname"),
    [("", ""), (DOMAIN, ""), ("www.example.com", "www.example.com")],
    ids=["domain", "self-steal-on-domain", "self-steal-own-name"],
)
def test_domain_mode_publishes_xhttp_and_wss_on_the_domain(sni: str, decoy_hostname: str) -> None:
    plan = compile_topology(_domain_intent(sni=sni, overrides=("direct", "block")))
    by_id = {item.logical_id: item.payload for item in plan.resources}
    endpoints = {payload.protocol: payload for payload in by_id.values() if isinstance(payload, ClientEndpointPayload)}
    workload = next(payload for payload in by_id.values() if isinstance(payload, NodeWorkloadPayload))

    assert {(endpoints[kind].address, endpoints[kind].sni, endpoints[kind].host) for kind in ("xhttp", "wss")} == {
        (DOMAIN, DOMAIN, DOMAIN)
    }
    assert endpoints["wss"].path.startswith("/") and endpoints["hysteria2"].address == ""
    assert endpoints["reality"].sni == (sni or "www.google.com")
    reality = next(inbound for inbound in workload.profile.inbounds if inbound.protocol == "reality")
    assert bool(reality.reality_dest_port) == bool(sni)
    assert [rule.target_tag for rule in workload.profile.routing_rules] == ["direct", "block"]
    decoys = {
        payload.tls_hostname
        for payload in by_id.values()
        if isinstance(payload, NginxArtifactPayload) and payload.layer == "http" and not payload.routes
    }
    assert decoys == ({decoy_hostname} if decoy_hostname else set())
    assert not {payload.kind for payload in by_id.values()} & {"host", "profile", "node", "control_plane_runtime"}


def test_control_domain_reaches_the_service_by_name() -> None:
    plan = compile_topology(_with_control_domain(_domain_intent(sni=DOMAIN)))
    service = next(item.payload for item in plan.resources if isinstance(item.payload, ControlServicePayload))
    stream = next(
        item.payload
        for item in plan.resources
        if isinstance(item.payload, NginxArtifactPayload) and item.payload.listener_port == 443
    )

    assert service.public_hostname == PANEL
    assert any(route.server_names == ["", PANEL] for route in stream.routes)


@pytest.mark.parametrize(("host", "observed"), [(PANEL, True), (ADDRESS, False)], ids=["domain-url", "stale-ip-url"])
def test_control_service_readiness_requires_the_url_on_its_domain(host: str, observed: bool) -> None:
    """A URL left on the IP would hand agents and links a certificate for another name."""
    from meridian.cluster import ClusterConfig
    from meridian.reconciler.resources import build_resource_actions
    from meridian.servers import ServerEntry
    from meridian.setup.control_service import control_service_ready
    from tests.support.mock_connection import MockConnection

    plan = compile_topology(_with_control_domain(_intent()))
    action = next(item for item in build_resource_actions(plan, 1) if item.resource.logical_id == "control:service")
    cluster = ClusterConfig()
    cluster.panel.server_ip, cluster.panel.api_token, cluster.panel.secret_path = ADDRESS, "token", "secret"
    cluster.panel.url = f"https://{host}/secret"
    conn = MockConnection()

    assert (
        control_service_ready(action, plan, cluster, ServerEntry(host=ADDRESS, user="root", id="srv-a"), conn) is False
    )
    assert bool(conn.calls) is observed  # only a matching URL goes on to read the attestation


def test_control_bootstrap_scopes_the_console_to_the_secret_path(tmp_home, monkeypatch) -> None:
    """The web console's session cookie needs the path nginx strips; without it sign-in is refused."""
    import meridian.provision as provision
    from meridian.cluster import ClusterConfig
    from meridian.servers import ServerEntry
    from meridian.setup.control_service import bootstrap_control_service
    from tests.support.mock_connection import MockConnection

    captured = []

    class Capture:
        def __init__(self, steps) -> None:
            pass

        def run(self, conn, ctx, **_: object) -> list:
            captured.append(ctx.control_service.config)
            raise RuntimeError("stop after the context is built")

    monkeypatch.setattr(provision, "Provisioner", Capture)
    plan = compile_topology(_intent())
    payload = next(item.payload for item in plan.resources if isinstance(item.payload, ControlServicePayload))
    cluster = ClusterConfig()
    cluster.panel.secret_path = "0123abcd"
    entry = ServerEntry(host=ADDRESS, user="root", id="srv-a")

    with pytest.raises(RuntimeError, match="stop after"):
        bootstrap_control_service(
            payload, plan, cluster, entry, MockConnection(), harden=False, reality_port=10443, reality_sni="a.example"
        )

    assert captured[0].public_path == "/0123abcd"


def _control_only_fleet():
    """The control server runs only the service: exit `de` behind a relay and a routing gateway."""
    relay = TransparentRelayIntent(id="r1", hop_server_refs=["srv-r"], exit_ref="de", protocol_path_ref="de-reality")
    gateway = RoutingGatewayIntent(id="gw", server_ref="srv-g", bridge_path_ref="de-reality")
    return _intent(
        exits=[_second_exit()], default_egress_ref="de", transparent_relays=[relay], routing_gateways=[gateway]
    )


def _on_remnawave(intent):
    return intent.model_copy(update={"control": intent.control.model_copy(update={"backend": "remnawave"})})


def test_a_control_server_without_an_exit_runs_only_the_service() -> None:
    """Its bootstrap and 443 stream match a panel-only server's: no Reality route, default backend ports."""
    from meridian.setup.control_plane import bootstrap_profile_values

    meridian = compile_topology(_control_only_fleet())
    panel = compile_topology(_on_remnawave(_control_only_fleet()))

    def control_stream(plan):
        return next(
            item.payload
            for item in plan.resources
            if isinstance(item.payload, NginxArtifactPayload) and item.payload.server_ref == "srv-a"
        )

    on_control = {
        item.payload.kind for item in meridian.resources if getattr(item.payload, "server_ref", "") == "srv-a"
    }
    assert on_control == {"server_baseline", "firewall_rule", "control_service", "nginx_artifact"}
    assert control_stream(meridian) == control_stream(panel)
    assert bootstrap_profile_values(meridian) == bootstrap_profile_values(panel)


def test_every_exit_gets_its_own_agent_and_workload() -> None:
    plan = compile_topology(_intent(exits=[*_intent().exits, _second_exit()]))
    placed = {
        (item.payload.kind, item.payload.server_ref)
        for item in plan.resources
        if item.payload.kind in {"agent_runtime", "node_workload"}
    }  # type: ignore[union-attr]

    assert placed == {
        ("agent_runtime", "srv-a"),
        ("node_workload", "srv-a"),
        ("agent_runtime", "srv-b"),
        ("node_workload", "srv-b"),
    }


def test_a_routing_gateway_is_an_agent_node_whose_workload_carries_its_routes() -> None:
    plan = compile_topology(gateway_fleet_intent())
    by_id = {item.logical_id: item for item in plan.resources}
    gateway = by_id["workload:gw"]
    profile = gateway.payload.profile  # type: ignore[union-attr]
    panel_kinds = {"service_user", "routing_gateway", "egress_pool", "route_rule", "internal_squad", "probe"}

    assert not {item.payload.kind for item in plan.resources} & panel_kinds
    assert by_id["agent:gw"].payload.server_ref == "srv-g"  # type: ignore[union-attr]
    # Each target exit's workload (and with it its keys and bridge) comes first.
    assert {"agent:gw", "workload:main", "workload:de"} <= set(gateway.dependencies)
    assert [edge.target_workload_ref for edge in profile.service_outbounds] == ["de", "main"]
    assert [rule.route_id for rule in profile.routing_rules] == ["ads", "local", "video", "default"]
    assert [balancer.pool_id for balancer in profile.egress_balancers] == ["both"]
    entry = by_id["endpoint:gw:entry:direct"].payload
    assert (entry.workload_ref, entry.address_server_ref) == ("gw", "srv-g")  # type: ignore[union-attr]
    restricted = {
        (item.payload.server_ref, tuple(item.payload.source_server_refs))  # type: ignore[union-attr]
        for item in plan.resources
        if item.payload.kind == "firewall_rule" and item.payload.source_server_refs  # type: ignore[union-attr]
    }
    assert restricted == {("srv-a", ("srv-g",)), ("srv-b", ("srv-g",))}  # the bridges; no node-API rule


def test_relay_endpoint_takes_its_keys_from_the_exit_and_its_address_from_the_relay() -> None:
    plan = compile_topology(_intent(transparent_relays=[_relay()]))
    relay = next(item for item in plan.resources if item.logical_id == "endpoint:r1:main-reality")

    assert relay.payload.workload_ref == "main"  # type: ignore[union-attr]
    assert relay.payload.address_server_ref == "srv-r"  # type: ignore[union-attr]
    assert "workload:main" in relay.dependencies
    assert any(item.payload.kind == "realm_hop" for item in plan.resources)


def test_default_backend_stays_out_of_saved_intent() -> None:
    """Existing cluster.yml files and contracts never see the hidden switch."""
    remnawave = single_server_intent(None, server_ref="srv-a")

    assert "backend" not in remnawave.model_dump(mode="json")["control"]
    assert _intent().model_dump(mode="json")["control"]["backend"] == "meridian"
    assert "control" not in DeployRequest().model_dump()


def test_redeploy_cannot_switch_the_control_backend() -> None:
    existing = single_server_intent(None, server_ref="srv-a", address=ADDRESS)

    with pytest.raises(LocalStateError, match="cannot change"):
        request_intent(existing, DeployRequest(control="meridian"), server_ref="srv-a", address=ADDRESS)


def test_redeploy_keeps_the_meridian_backend_without_the_switch() -> None:
    intent = request_intent(_intent(), DeployRequest(), server_ref="srv-a", address=ADDRESS)

    assert intent.control.backend == "meridian"


def _fleet():
    return _intent(exits=[*_intent().exits, _second_exit()], transparent_relays=[_relay()])


def _with_control_domain(intent):
    return intent.model_copy(update={"control": intent.control.model_copy(update={"public_hostname": PANEL})})


def _control_domain_fleet():
    return _with_control_domain(_domain_fleet("www.example.com")())


def _domain_fleet(sni: str):
    def build():
        domain = _domain_intent(sni=sni, overrides=("direct", "block"))
        return domain.model_copy(update={"exits": [*domain.exits, _second_exit()], "transparent_relays": [_relay()]})

    return build


def _gateway_fleet():
    """Both exits behind a routing gateway with a pool and ordered rules, plus a relay to `main`."""
    return gateway_fleet_intent().model_copy(update={"transparent_relays": [_relay()]})


def _warp_fleet():
    """WARP as one exit's default egress and as another's per-destination override."""
    fleet = _fleet()
    main, de = fleet.exits
    return fleet.model_copy(
        update={
            "exits": [
                main.model_copy(update={"warp": True}),
                de.model_copy(update={"egress_overrides": _overrides("warp", "direct")}),
            ]
        }
    )


SERVERS = {**GATEWAY_SERVERS, "srv-r": "198.51.100.30"}
ROLES = {ADDRESS: "control", "198.51.100.20": "exit", "198.51.100.30": "relay", "198.51.100.40": "gateway"}
PARITY_FLEETS = {
    "ip": _fleet,
    "domain": _domain_fleet(""),
    "self-steal-on-domain": _domain_fleet(DOMAIN),
    "self-steal-own-name": _domain_fleet("www.example.com"),
    "control-domain": _control_domain_fleet,
    "warp": _warp_fleet,
    "gateway": _gateway_fleet,
    "control-only": _control_only_fleet,
}
# The domain exit sits on the control server: its WSS and XHTTP routes are on the domain.
DOMAIN_SHAPES = {"domain", "self-steal-on-domain", "self-steal-own-name", "control-domain"}


@pytest.mark.parametrize(
    ("shape", "target"),
    [(shape, target) for shape in PARITY_FLEETS for target in ROLES],
    ids=[f"{shape}-{role}" for shape in PARITY_FLEETS for role in ROLES.values()],
)
def test_verification_sees_a_control_service_fleet_like_a_panel_fleet(tmp_path, shape: str, target: str) -> None:
    """`meridian test` dropped route checks on a control-service fleet until it read client endpoints."""
    from meridian.cluster import ClusterConfig
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.verification import resolve_deployment_verification_context

    registry = ServerRegistry(tmp_path / "servers.json")
    for ref, host in SERVERS.items():
        registry.add(ServerEntry(host=host, user="root", id=ref))
    meridian = PARITY_FLEETS[shape]()
    panel = _on_remnawave(meridian)

    contexts = []
    for intent in (meridian, panel):
        cluster = ClusterConfig()
        cluster.topology_intent = intent
        contexts.append(resolve_deployment_verification_context(registry, target, cluster=cluster)[1])

    assert contexts[0] == contexts[1]
    members = {SERVERS[ref] for ref in deployment_server_refs(meridian)}
    assert contexts[0].https_routes
    assert (contexts[0].kind == "v4") == (target in members)
    assert ("wss" in contexts[0].protocols) == (shape in DOMAIN_SHAPES and target == ADDRESS)
    assert (DOMAIN in contexts[0].domains) == (shape in DOMAIN_SHAPES and target == ADDRESS)
    assert (PANEL in contexts[0].domains) == (shape == "control-domain" and target == ADDRESS)
