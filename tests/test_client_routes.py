"""Client routing changes must not broaden another subscription's rules."""

from dataclasses import replace

import pytest

from meridian.compiler import delivery
from meridian.compiler.models import SubscriptionTemplatePayload
from tests.support.topology import compile_panel_pass, single_exit_intent


def test_bittorrent_bypass_changes_only_xray_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    intent = single_exit_intent()
    current = compile_panel_pass(intent)
    without_protocols = tuple(replace(route, protocols=()) for route in delivery.DEFAULT_CLIENT_ROUTES)
    # Simulate the previous routing policy through every renderer, including
    # formats that must ignore the new protocol-only entry entirely.
    for name in ("xray_client_rules", "mihomo_client_rules", "singbox_client_rules"):
        renderer = getattr(delivery, name)
        monkeypatch.setattr(delivery, name, lambda renderer=renderer: renderer(without_protocols))
    previous = {resource.logical_id: resource for resource in compile_panel_pass(intent).resources}

    xray_templates = 0
    bittorrent = {"type": "field", "protocol": ["bittorrent"], "outboundTag": "DIRECT"}
    for resource in current.resources:
        old = previous.pop(resource.logical_id)
        payload = resource.payload
        if not isinstance(payload, SubscriptionTemplatePayload) or payload.template_type != "XRAY_JSON":
            # Includes complete Mihomo/sing-box templates, subscription settings,
            # and all Host projections used to produce Base64 links.
            assert resource == old
            continue
        xray_templates += 1
        assert payload.template_json is not None
        config = payload.template_json
        rules = config["routing"]["rules"]
        assert rules[-3:] == [
            {"type": "field", "network": "udp", "port": "443", "outboundTag": "BLOCK"},
            bittorrent,
            {"type": "field", "network": "tcp,udp", "balancerTag": "MERIDIAN_AUTO"},
        ]
        assert rules.count(bittorrent) == 1
        for inbound in config["inbounds"]:
            sniffing = inbound["sniffing"]
            assert sniffing["enabled"] and sniffing["routeOnly"]
            assert not sniffing.get("metadataOnly", False)
            assert "bittorrent" not in sniffing["destOverride"]
        # No other Xray settings change, including the fail-closed fallback.
        assert isinstance(old.payload, SubscriptionTemplatePayload)
        assert {**config, "routing": {**config["routing"], "rules": rules[:-2] + rules[-1:]}} == (
            old.payload.template_json
        )
    assert not previous
    assert xray_templates == 2  # Primary and alternate-fingerprint subscription.


@pytest.mark.parametrize("action,outbound", [("direct", "DIRECT"), ("block", "BLOCK")])
def test_protocol_matchers_never_become_match_all_rules_in_other_formats(action, outbound) -> None:
    route = delivery.ClientRoute(action, protocols=("bittorrent",))
    assert delivery.xray_client_rules((route,)) == [
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": outbound}
    ]
    assert delivery.mihomo_client_rules((route,)) == []
    assert delivery.singbox_client_rules((route,)) == []

    with_domain = replace(route, domains=("domain:example.org",))
    for renderer in (delivery.mihomo_client_rules, delivery.singbox_client_rules):
        assert renderer((with_domain,)) == renderer((replace(with_domain, protocols=()),))
