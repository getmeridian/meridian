"""Per-destination egress overrides: intent validation, compilation, rendering, and setup round-trips."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.cluster import RealityKeyBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import ConfigProfilePayload, NodeRuntimePayload
from meridian.core.setup import SetupDraft
from meridian.core.topology import (
    EgressOverrideIntent,
    SetupIntent,
)
from meridian.setup.editor import add_egress_override_to_intent, remove_egress_override_from_intent
from meridian.xray_workload import render_workload_config
from tests.support.topology import single_exit_intent


def _intent(*, warp: bool = False, geo_block: bool = False, overrides: list[EgressOverrideIntent] | None = None):
    return single_exit_intent(
        control="srv-exit",
        users=["alice"],
        exit_fields={"warp": warp, "geo_block": geo_block, "egress_overrides": overrides or []},
    )


def _payload(intent: SetupIntent, payload_type: type):
    payloads = [resource.payload for resource in compile_topology(intent).resources]
    return next(payload for payload in payloads if isinstance(payload, payload_type))


def _render(intent: SetupIntent) -> dict:
    keys = RealityKeyBinding(public_key="public-a", private_key="private-a", short_id="0123456789abcdef")
    return render_workload_config(_payload(intent, ConfigProfilePayload), reality_keys=keys)


def test_override_values_are_canonicalized_and_geo_prefixes_dropped() -> None:
    domains = EgressOverrideIntent(id="warp", match="domain", values=["Netflix.COM."], action="warp")
    sites = EgressOverrideIntent(id="ads", match="geosite", values=["geosite:category-ads-all"], action="block")

    assert domains.values == ["netflix.com"]
    assert sites.values == ["category-ads-all"]


@pytest.mark.parametrize(
    ("match", "value"),
    [("domain", "not a domain"), ("geosite", "bad name;rm"), ("geoip", "")],
)
def test_invalid_override_values_are_refused(match: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EgressOverrideIntent(id="x", match=match, values=[value], action="direct")  # type: ignore[arg-type]


def test_an_exit_refuses_two_overrides_with_one_id() -> None:
    override = EgressOverrideIntent(id="warp", match="domain", values=["example.com"], action="warp")

    with pytest.raises(ValidationError, match="egress override ID more than once"):
        _intent(overrides=[override, override])


def test_exit_without_overrides_compiles_exactly_as_before() -> None:
    profile = _payload(_intent(), ConfigProfilePayload)

    assert profile.outbound_tags == ["direct"]
    assert profile.routing_rules == []
    assert _payload(_intent(), NodeRuntimePayload).warp is False


def test_override_only_warp_installs_warp_but_keeps_direct_the_default() -> None:
    intent = _intent(overrides=[EgressOverrideIntent(id="ai", match="domain", values=["example.com"], action="warp")])

    config = _render(intent)

    assert _payload(intent, NodeRuntimePayload).warp is True
    assert [outbound["tag"] for outbound in config["outbounds"]] == ["direct", "block", "warp"]
    assert {"type": "field", "ruleTag": "override-ai", "outboundTag": "warp", "domain": ["domain:example.com"]} in (
        config["routing"]["rules"]
    )


def test_warp_default_exit_can_send_chosen_destinations_direct() -> None:
    intent = _intent(
        warp=True,
        overrides=[EgressOverrideIntent(id="local", match="geoip", values=["de"], action="direct")],
    )

    config = _render(intent)

    assert config["outbounds"][0]["tag"] == "warp"
    assert config["routing"]["rules"][1] == {
        "type": "field",
        "ruleTag": "override-local",
        "outboundTag": "direct",
        "ip": ["geoip:de"],
    }


def test_overrides_keep_operator_order_after_the_private_block_and_before_geo_block() -> None:
    intent = _intent(
        geo_block=True,
        overrides=[
            EgressOverrideIntent(id="first", match="geosite", values=["netflix"], action="warp"),
            EgressOverrideIntent(id="second", match="domain", values=["example.org"], action="block"),
        ],
    )

    rules = _render(intent)["routing"]["rules"]

    assert rules[0]["ip"] == ["geoip:private"]
    assert [rule.get("ruleTag") for rule in rules[1:3]] == ["override-first", "override-second"]
    assert rules[1]["domain"] == ["geosite:netflix"]
    assert rules[3]["domain"] == ["geosite:category-ru"]


def test_editor_numbers_default_ids_and_refuses_mixed_kinds() -> None:
    first, override = add_egress_override_to_intent(_intent(), "exit-a", action="warp", values=["example.com"])
    second, again = add_egress_override_to_intent(first, "exit-a", action="warp", values=["geosite:netflix"])

    assert (override.id, again.id) == ("warp", "warp-2")
    assert again.match == "geosite" and again.values == ["netflix"]
    assert [item.id for item in second.exits[0].egress_overrides] == ["warp", "warp-2"]
    with pytest.raises(ValueError, match="each kind with its own command"):
        add_egress_override_to_intent(_intent(), "exit-a", action="block", values=["example.com", "geoip:ir"])


def test_editor_refuses_unknown_exits_ids_and_bad_values() -> None:
    with pytest.raises(ValueError, match="not in the topology"):
        add_egress_override_to_intent(_intent(), "exit-z", action="warp", values=["example.com"])
    with pytest.raises(ValueError, match="Enter"):
        add_egress_override_to_intent(_intent(), "exit-a", action="warp", values=["geosite:bad name"])
    with pytest.raises(ValueError, match="no override named"):
        remove_egress_override_from_intent(_intent(), "exit-a", "missing")


def test_removing_an_override_returns_it_and_drops_warp_when_nothing_else_needs_it() -> None:
    with_override, _ = add_egress_override_to_intent(_intent(), "exit-a", action="warp", values=["example.com"])

    updated, removed = remove_egress_override_from_intent(with_override, "exit-a", "warp")

    assert removed.values == ["example.com"]
    assert updated.exits[0].egress_overrides == []
    assert _payload(updated, NodeRuntimePayload).warp is False


def test_setup_draft_round_trip_keeps_overrides() -> None:
    intent = _intent(overrides=[EgressOverrideIntent(id="ai", match="domain", values=["example.com"], action="warp")])

    assert SetupDraft.from_intent(intent).to_intent() == intent
