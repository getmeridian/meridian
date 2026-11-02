"""Reviewed delivery intent, portable rules, and honest format limits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.models import ServiceSettings
from meridian.control.subscriptions import render_subscription
from meridian.core.topology import DeliveryIntent
from tests.support.topology import single_exit_intent
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, UUID


def policy_data() -> dict:
    return {
        "id": "work",
        "preset": "full",
        "rules": [
            {"match": {"kind": "domain_suffix", "values": ["example.test"]}, "action": {"kind": "block"}},
            {"match": {"kind": "domain_full", "values": ["chat.example.test"]}, "action": {"kind": "proxy"}},
            {"match": {"kind": "domain_keyword", "values": ["video"]}, "action": {"kind": "direct"}},
            {"match": {"kind": "cidr", "values": ["198.51.100.0/24"]}, "action": {"kind": "direct"}},
            {"match": {"kind": "port", "values": [{"start": 8443, "end": 8445}]}, "action": {"kind": "block"}},
            {"match": {"kind": "network", "values": ["udp"]}, "action": {"kind": "proxy"}},
        ],
        "dns": {
            "resolvers": [{"transport": "doh", "address": "198.51.100.53", "path": "/dns-query"}],
            "fake_ip": False,
        },
    }


def delivery_data() -> dict:
    return {"delivery_policies": [policy_data()], "default_policy": "work"}


def test_named_policies_reach_reviewed_service_settings() -> None:
    delivery = DeliveryIntent.model_validate(delivery_data())
    plan = compile_topology(single_exit_intent(delivery=delivery))
    settings = next(
        resource.payload for resource in plan.resources if isinstance(resource.payload, ServiceSettingsPayload)
    )
    assert settings.default_policy == "work"
    assert settings.delivery_policies == delivery.delivery_policies
    assert settings.to_data()["delivery_policies"][0]["rules"][0]["match"]["kind"] == "domain_suffix"


def test_delivery_settings_keep_profile_declaration_fences(tmp_path: Path) -> None:
    from meridian.control.client import ControlClient, ControlServiceConflict
    from meridian.core.access_profile_views import AccessProfileReviewRequest
    from meridian.core.access_profiles import AccessProfileIntent, profile_declarations_hash
    from tests.support.control_service import ADMIN_TOKEN, serve_control

    declarations = profile_declarations_hash([AccessProfileIntent(id="limited", grants=[])])
    settings = ServiceSettings.model_validate(
        {
            "profile_title": "Meridian",
            "access_profiles": {"limited": []},
            "access_profile_declarations_hash": declarations,
            "profile_policies": {"limited": "work"},
            **delivery_data(),
        }
    )
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(settings, expected_profile_declarations_hash=declarations)
        assert admin.settings() == settings
        changed = ServiceSettings.model_validate(
            {
                **settings.to_data(),
                "access_profile_declarations_hash": profile_declarations_hash(
                    [AccessProfileIntent(id="limited", grants=["exit:exit-a"])]
                ),
            }
        )
        review = admin.preview_access_profiles(AccessProfileReviewRequest(access_profiles=settings.access_profiles))
        admin.put_settings(changed, expected_access_hash=review.access_hash)
        with pytest.raises(ControlServiceConflict, match="declarations changed"):
            admin.put_settings(settings, expected_profile_declarations_hash=declarations)
        assert admin.settings() == changed


@pytest.mark.parametrize("document", ["xray-json", "mihomo", "stash", "singbox"])
def test_every_structured_format_compiles_the_same_ordered_policy(document: str) -> None:
    from meridian.core.delivery_policies import resolve_delivery_policy

    settings = ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()})
    response = render_subscription(
        SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID),
        FLEETS["fleet"].endpoints,
        document,
        profile_title="Meridian",
        delivery_policy=resolve_delivery_policy(settings),
    )
    if document == "xray-json":
        for config in json.loads(response.body):
            rules = config["routing"]["rules"]
            assert {"type": "field", "domain": ["domain:example.test"], "outboundTag": "BLOCK"} in rules
            assert any(r.get("domain") == ["full:chat.example.test"] for r in rules)
            assert any(r.get("domain") == ["keyword:video"] for r in rules)
            assert config["dns"]["servers"] == ["https://198.51.100.53/dns-query"]
    elif document in ("mihomo", "stash"):
        config = yaml.safe_load(response.body)
        assert config["rules"].index("DOMAIN-SUFFIX,example.test,REJECT") < config["rules"].index(
            "DOMAIN,chat.example.test,Meridian"
        )
        assert "DOMAIN-KEYWORD,video,DIRECT" in config["rules"]
        assert "IP-CIDR,198.51.100.0/24,DIRECT,no-resolve" in config["rules"]
        assert config["dns"]["nameserver"] == ["https://198.51.100.53/dns-query"]
    else:
        config = json.loads(response.body)
        assert {"domain_suffix": ["example.test"], "action": "reject"} in config["route"]["rules"]
        assert {"domain": ["chat.example.test"], "outbound": "Meridian"} in config["route"]["rules"]
        assert {"domain_keyword": ["video"], "outbound": "direct"} in config["route"]["rules"]
        assert config["dns"]["servers"][0]["type"] == "https"
    assert "rule_set" not in response.body and "rule-providers" not in response.body


def test_assignment_has_one_explicit_precedence_and_refuses_conflicting_profiles() -> None:
    from meridian.core.delivery_policies import resolve_delivery_policy

    settings = ServiceSettings.model_validate(
        {"profile_title": "Meridian", **delivery_data(), "profile_policies": {"family": "full", "work": "work"}}
    )
    assert resolve_delivery_policy(settings).policy.id == "work"
    assert resolve_delivery_policy(settings, profiles=["family"]).policy.id == "full"
    assert (
        resolve_delivery_policy(settings, profiles=["family", "work"], person_override="ru-split").policy.id
        == "ru-split"
    )
    with pytest.raises(ValueError, match="different delivery policies"):
        resolve_delivery_policy(settings, profiles=["family", "work"])
    with pytest.raises(ValueError, match="Unknown delivery policy"):
        resolve_delivery_policy(settings, person_override="missing")


@pytest.mark.parametrize(
    "bad_match",
    [
        {"kind": "domain_suffix", "values": ["*.example.test"]},
        {"kind": "domain_keyword", "values": ["x,REJECT"]},
        {"kind": "domain_full", "values": ["https://example.test/"]},
        {"kind": "geosite", "values": ["missing-list"]},
        {"kind": "geoip", "values": ["missing-list"]},
        {"kind": "cidr", "values": ["198.51.100.1/24"]},
        {"kind": "port", "values": [{"start": 8445, "end": 8443}]},
        {"kind": "port", "values": [{"start": True, "end": 443}]},
        {"kind": "network", "values": ["icmp"]},
        {"kind": "regex", "values": [".*"]},
    ],
)
def test_matchers_refuse_unbounded_or_unrepresentable_values(bad_match: dict) -> None:
    from meridian.core.delivery_policies import DeliveryPolicy

    data = policy_data()
    data["rules"] = [{"match": bad_match, "action": {"kind": "block"}}]
    with pytest.raises(ValidationError):
        DeliveryPolicy.model_validate(data)


@pytest.mark.parametrize(
    "dns",
    [
        {"resolvers": []},
        {"resolvers": [{"transport": "doh", "address": "resolver.example.test"}]},
        {"resolvers": [{"transport": "doh", "address": "198.51.100.53", "path": "/dns-query?token=secret"}]},
        {"resolvers": [{"transport": "dot", "address": "198.51.100.53", "port": 0}]},
        {"resolvers": [{"transport": "plain", "address": "0.0.0.0"}]},
        {"resolvers": [{"transport": "plain", "address": "198.51.100.53"}], "fake_ip": "yes"},
    ],
)
def test_dns_choices_validate_before_review(dns: dict) -> None:
    from meridian.core.delivery_policies import DeliveryPolicy

    with pytest.raises(ValidationError):
        DeliveryPolicy.model_validate({**policy_data(), "dns": dns})


def test_policy_and_rule_counts_are_bounded_and_presets_are_reserved() -> None:
    from meridian.core.delivery_policies import DeliveryPolicy

    data = policy_data()
    with pytest.raises(ValidationError):
        DeliveryPolicy.model_validate({**data, "rules": data["rules"] * 22})
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate({"delivery_policies": [{**data, "id": "full"}]})
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate({"delivery_policies": [data, data]})
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate({**delivery_data(), "default_policy": "missing"})


def test_default_and_explicit_builtin_policies_keep_every_subscription_byte() -> None:
    from meridian.core.delivery_policies import resolve_delivery_policy

    for preset in ("ru-split", "full"):
        settings = ServiceSettings(profile_title="Meridian", client_routing=preset)
        selected = resolve_delivery_policy(settings)
        for document in ("base64", "xray-json", "mihomo", "stash", "singbox"):
            kwargs = {"profile_title": "Meridian", "client_routing": preset}
            user = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID)
            endpoints = FLEETS["fleet"].endpoints
            assert render_subscription(user, endpoints, document, **kwargs) == render_subscription(
                user, endpoints, document, delivery_policy=selected, **kwargs
            )


def test_singbox_omissions_never_widen_a_geodata_match_to_its_port_or_network() -> None:
    from meridian.core.delivery_capabilities import represent_policy_dns, represent_policy_routes
    from meridian.core.delivery_policies import resolve_delivery_policy

    data = policy_data()
    data["rules"] = [
        {
            "match": {"kind": "geosite", "values": ["category-ru"]},
            "action": {"kind": "block"},
            "network": "tcp",
            "ports": [{"start": 8443}],
        }
    ]
    data["dns"] = {"resolvers": [{"transport": "dot", "address": "198.51.100.53"}], "fake_ip": True}
    policy = resolve_delivery_policy(
        ServiceSettings.model_validate(
            {"profile_title": "Meridian", "delivery_policies": [data], "default_policy": "work"}
        )
    )
    routes, omitted = represent_policy_routes(policy, "singbox")
    assert not any(route.source_rule == 1 for route in routes)
    assert any(item.reason == "geosite_not_supported" and item.rule == 2 for item in omitted)
    _, fake_ip, xray_omitted = represent_policy_dns(policy, "xray-json")
    assert not fake_ip
    assert {item.reason for item in xray_omitted} == {"dns_transport_not_supported", "fake_ip_not_supported"}
    _, base64_omitted = represent_policy_routes(policy, "base64")
    assert [item.reason for item in base64_omitted] == ["rules_not_carried"]


@pytest.mark.parametrize("document", ["xray-json", "mihomo", "stash", "singbox"])
def test_via_exit_targets_only_eligible_members_and_fails_closed_when_unavailable(document: str) -> None:
    from meridian.core.delivery_policies import resolve_delivery_policy

    data = policy_data()
    data["rules"] = [
        {"match": {"kind": "domain_suffix", "values": ["example.test"]}, "action": {"kind": "exit", "exit": "west"}}
    ]
    policy = resolve_delivery_policy(
        ServiceSettings.model_validate(
            {"profile_title": "Meridian", "delivery_policies": [data], "default_policy": "work"}
        )
    )
    endpoints = [
        endpoint.model_copy(update={"exit_ref": "west" if index == 0 else "east"})
        for index, endpoint in enumerate(FLEETS["fleet"].endpoints)
    ]
    user = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID)
    response = render_subscription(user, endpoints, document, profile_title="Meridian", delivery_policy=policy)
    if document == "xray-json":
        configs = json.loads(response.body)
        for config in configs:
            rule = next(rule for rule in config["routing"]["rules"] if rule.get("domain") == ["domain:example.test"])
            balancer = next(
                (balancer for balancer in config["routing"]["balancers"] if balancer["tag"] == rule.get("balancerTag")),
                None,
            )
            if balancer is None:
                assert rule["outboundTag"] == "BLOCK"
            else:
                assert len(balancer["selector"]) == 1
                assert balancer["fallbackTag"] == "BLOCK"
        assert any(
            any(
                rule.get("outboundTag") == "BLOCK" and rule.get("domain") == ["domain:example.test"]
                for rule in config["routing"]["rules"]
            )
            for config in configs[1:]
        )
    elif document in ("mihomo", "stash"):
        config = yaml.safe_load(response.body)
        group = next(group for group in config["proxy-groups"] if group["name"].startswith("Meridian exit west"))
        assert len(group["proxies"]) == 1 and "exclude-filter" not in group
        assert f"DOMAIN-SUFFIX,example.test,{group['name']}" in config["rules"]
    else:
        config = json.loads(response.body)
        group = next(outbound for outbound in config["outbounds"] if outbound["tag"].startswith("Meridian exit west"))
        assert len(group["outbounds"]) == 1
    missing = render_subscription(
        user,
        [endpoint.model_copy(update={"exit_ref": "east"}) for endpoint in endpoints],
        document,
        profile_title="Meridian",
        delivery_policy=policy,
    )
    assert "MERIDIAN_EXIT_west" not in missing.body and "Meridian exit west" not in missing.body


def test_undeclared_exit_and_profile_defaults_are_refused_before_compile() -> None:
    from meridian.core.topology import SetupIntent

    data = single_exit_intent(delivery=DeliveryIntent.model_validate(delivery_data())).to_data()
    data["delivery"]["profile_policies"] = {"missing": "work"}
    with pytest.raises(ValidationError, match="unknown access profiles"):
        SetupIntent.model_validate(data)
    data["delivery"].pop("profile_policies")
    data["delivery"]["delivery_policies"][0]["rules"][0]["action"] = {"kind": "exit", "exit": "missing"}
    with pytest.raises(ValidationError, match="unknown exits"):
        SetupIntent.model_validate(data)
