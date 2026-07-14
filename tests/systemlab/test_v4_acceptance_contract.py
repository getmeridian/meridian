from __future__ import annotations

from pathlib import Path

import yaml

from meridian.compiler import compile_topology
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressPoolPayload,
    HostPayload,
    RealmHopPayload,
    RouteRulePayload,
    SubscriptionTemplatePayload,
)
from tests.systemlab.topology import build_systemlab_intent

_COMPOSE_PATH = Path(__file__).parent / "compose.yml"


def _server_refs() -> dict[str, str]:
    return {
        "exit_a": "srv-exit-a",
        "exit_b": "srv-exit-b",
        "relay_a": "srv-relay-a",
        "relay_b": "srv-relay-b",
        "gateway": "srv-gateway",
    }


def test_systemlab_intent_compiles_every_v4_acceptance_path() -> None:
    plan = compile_topology(build_systemlab_intent(_server_refs()))

    profiles = [
        resource.payload
        for resource in plan.resources
        if isinstance(
            resource.payload,
            ConfigProfilePayload,
        )
    ]
    assert {profile.workload_id for profile in profiles} == {"exit-a", "exit-b", "gateway-a"}

    hops = sorted(
        (resource.payload for resource in plan.resources if isinstance(resource.payload, RealmHopPayload)),
        key=lambda hop: hop.hop_index,
    )
    assert [(hop.server_ref, hop.advertised) for hop in hops] == [
        ("srv-relay-a", True),
        ("srv-relay-b", False),
    ]

    visible_hosts = [
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, HostPayload)
        and not resource.payload.is_hidden
        and not resource.payload.xray_json_template_ref
    ]
    assert {host.address_server_ref for host in visible_hosts} == {
        "srv-exit-a",
        "srv-exit-b",
        "srv-relay-a",
        "srv-gateway",
    }
    assert all(host.address_server_ref != "srv-relay-b" for host in visible_hosts)

    pools = [resource.payload for resource in plan.resources if isinstance(resource.payload, EgressPoolPayload)]
    assert len(pools) == 1
    assert pools[0].exit_refs == ["exit-a", "exit-b"]
    assert pools[0].fail_closed is True

    routes = [resource.payload for resource in plan.resources if isinstance(resource.payload, RouteRulePayload)]
    assert sorted((route.priority, route.target_type) for route in routes) == [(10, "outbound"), (100, "balancer")]

    templates = [
        resource.payload.template_type
        for resource in plan.resources
        if isinstance(
            resource.payload,
            SubscriptionTemplatePayload,
        )
    ]
    assert sorted(templates) == ["MIHOMO", "XRAY_JSON"]


def test_compose_provides_every_isolated_topology_server() -> None:
    compose = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    server_names = {
        "exit-a",
        "exit-b",
        "relay-a",
        "relay-b",
        "gateway-a",
    }

    assert server_names < set(services)
    addresses = [services[name]["networks"]["labnet"]["ipv4_address"] for name in server_names]
    assert len(addresses) == len(set(addresses))
    assert set(services["controller"]["depends_on"]) >= server_names
