from __future__ import annotations

import re
from pathlib import Path

import yaml

from meridian.compiler import compile_topology
from meridian.compiler.models import (
    ClientEndpointPayload,
    ConfigProfilePayload,
    EgressPoolPayload,
    HostPayload,
    RealmHopPayload,
    RouteRulePayload,
    SubscriptionTemplatePayload,
)
from meridian.config import XRAY_VERSION
from tests.support.topology import compile_panel_pass
from tests.systemlab.topology import build_systemlab_intent

_COMPOSE_PATH = Path(__file__).parent / "compose.yml"
_CONTROLLER_DOCKERFILE = Path(__file__).parent / "images/controller/Dockerfile"
_BOOTSTRAP_PATH = Path(__file__).parent / "scripts/stages/00-bootstrap.sh"
_RESILIENCE_PATH = Path(__file__).parent / "scripts/stages/30-resilience.sh"
_SUBSCRIPTIONS_PATH = Path(__file__).parent / "scripts/stages/20-subscriptions.sh"
_CLIENT_DOCS = Path(__file__).parents[2] / "website/src/content/docs/en/client-management.md"
_INTERRUPTED_RESOURCE = "endpoint:exit-a:exit-a-reality:direct"


def _server_refs() -> dict[str, str]:
    return {
        "exit_a": "srv-exit-a",
        "exit_b": "srv-exit-b",
        "relay_a": "srv-relay-a",
        "relay_b": "srv-relay-b",
        "gateway": "srv-gateway",
    }


def test_systemlab_intent_compiles_every_v4_acceptance_path() -> None:
    intent = build_systemlab_intent(_server_refs())
    assert {path.reality_sni for exit_ in intent.exits for path in exit_.paths} == {"www.google.com"}
    plan = compile_panel_pass(intent)

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
    interrupted = next(
        resource.payload
        for resource in compile_topology(intent).resources
        if resource.logical_id == _INTERRUPTED_RESOURCE
    )
    assert isinstance(interrupted, ClientEndpointPayload)
    assert interrupted.address_server_ref == "srv-exit-a"

    pools = [resource.payload for resource in plan.resources if isinstance(resource.payload, EgressPoolPayload)]
    assert len(pools) == 1
    assert pools[0].exit_refs == ["exit-a", "exit-b"]
    assert pools[0].fail_closed is True

    routes = [resource.payload for resource in plan.resources if isinstance(resource.payload, RouteRulePayload)]
    assert sorted((route.priority, route.target_type) for route in routes) == [(10, "balancer"), (100, "balancer")]
    probe_route = next(route for route in routes if route.route_id == "route-probe-via-pool")
    assert probe_route.match_values == ["ifconfig.me"]
    assert probe_route.target_ref == "pool-a"
    assert probe_route.target_tag == pools[0].balancer_tag

    templates = [
        resource.payload.template_type
        for resource in plan.resources
        if isinstance(
            resource.payload,
            SubscriptionTemplatePayload,
        )
    ]
    # The second Xray JSON template balances the alternate-fingerprint copies.
    assert sorted(templates) == ["MIHOMO", "SINGBOX", "XRAY_JSON", "XRAY_JSON"]


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


def test_controller_trusts_the_local_acme_certificate_chain() -> None:
    dockerfile = _CONTROLLER_DOCKERFILE.read_text(encoding="utf-8")
    bootstrap = _BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert "fixtures/pebble-ca.pem /usr/local/share/ca-certificates/pebble-ca.crt" in dockerfile
    assert "RUN update-ca-certificates" in dockerfile
    assert "ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" in dockerfile
    assert "https://pebble:15000/roots/0" in bootstrap
    assert '--cacert "$PEBBLE_ENDPOINT_CA"' in bootstrap
    assert 'openssl x509 -in "$PEBBLE_ISSUER_TMP" -noout' in bootstrap
    assert 'install -m 0644 "$PEBBLE_ISSUER_TMP" "$PEBBLE_ISSUER_CA"' in bootstrap
    assert "update-ca-certificates" in bootstrap


def test_every_agent_trusts_the_local_acme_chain_before_deploy() -> None:
    """Agents verify the control service against their unit's CA file; Pebble is no WebPKI CA."""
    bootstrap = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    deploy = bootstrap.index('meridian server add "$EXIT_A_IP"')

    assert bootstrap.index("NETAGENT_TLS_CA_FILE=/etc/ssl/certs/ca-certificates.crt") < deploy
    assert bootstrap.index("for host in $(all_server_ips)", bootstrap.index("Pebble issuance root trusted")) < deploy


def test_resilience_kills_a_real_apply_after_the_exact_endpoint_mutation() -> None:
    script = _RESILIENCE_PATH.read_text(encoding="utf-8")

    assert f"INTERRUPTED_RESOURCE={_INTERRUPTED_RESOURCE}" in script
    assert "MERIDIAN_TEST_AFTER_APPLY_RESOURCE" in script
    assert "MERIDIAN_TEST_AFTER_APPLY_MARKER" in script
    assert "for _ in $(seq 1 90)" in script
    assert "2>&1 &" in script
    assert 'kill -9 "$INTERRUPTED_APPLY_PID"' in script
    assert 'wait "$INTERRUPTED_APPLY_PID"' in script
    assert 'if [ "$INTERRUPTED_APPLY_STATUS" -eq 0 ]' in script
    assert 'assert checkpoint.status == "running"' in script
    assert "assert not cluster.pending_plan_hash" in script
    assert 'python3 "$SUBSCRIPTION_TEST" --automatic' in script
    assert 'checkpoint.status = "unknown"' not in script
    assert re.search(r"cluster\.pending_generation\s*=(?!=)", script) is None
    assert re.search(r"cluster\.pending_plan_hash\s*=(?!=)", script) is None
    assert "cluster.save()" not in script


def test_engine_checks_are_pinned_per_architecture_and_documented() -> None:
    """The docs promise the engine versions the lab checks with; a pin bump must update them."""
    pins = dict(re.findall(r"^ARG (\w+)=(\S+)$", _CONTROLLER_DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE))
    docs = _CLIENT_DOCS.read_text(encoding="utf-8")

    for engine in ("SING_BOX", "MIHOMO"):
        for arch in ("AMD64", "ARM64"):
            assert re.fullmatch(r"[0-9a-f]{64}", pins[f"{engine}_SHA256_{arch}"])
    assert f"sing-box {pins['SING_BOX_VERSION']}" in docs
    assert f"Mihomo {pins['MIHOMO_VERSION']}" in docs
    assert f"Xray {XRAY_VERSION}" in docs
    assert "check-client-engines.py" in _SUBSCRIPTIONS_PATH.read_text(encoding="utf-8")
