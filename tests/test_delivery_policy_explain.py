"""Policy previews and destination explanations use the same compiled rule order."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode
from meridian.control.models import ServiceSettings, TokenCreate
from meridian.control.tokens import ROOT
from meridian.core.delivery_explain import DeliveryExplainRequest, explain_delivery_policy
from meridian.core.delivery_geodata import GeoData, GeoDomain
from meridian.core.delivery_policies import DeliveryPolicySettings, resolve_delivery_policy
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import serve_tokens, token_request
from tests.support.topology import single_exit_intent
from tests.test_control_subscription_corpus import FLEETS
from tests.test_delivery_policies import delivery_data


@pytest.mark.parametrize("with_dns", [False, True])
@pytest.mark.parametrize(
    "destination, protocol, status, action, rule",
    [
        ("router.lan:443", "other", "matched", "direct", 1),
        ("chat.example.test:443", "other", "matched", "block", 2),
        ("video.example.org:443", "other", "matched", "direct", 4),
        ("198.51.100.10:443", "other", "matched", "direct", 5),
        ("peer.example.org:443", "other", "default", "proxy", None),
        ("peer.example.org:6881", "bittorrent", "matched", "direct", 9),
    ],
)
def test_xray_explains_the_name_pass_and_catchall_without_resolving_ips(
    with_dns: bool, destination: str, protocol: str, status: str, action: str, rule: int | None
) -> None:
    data = delivery_data()
    if not with_dns:
        data["delivery_policies"][0]["dns"] = None
    result = explain_delivery_policy(
        resolve_delivery_policy(DeliveryPolicySettings.model_validate(data)),
        DeliveryExplainRequest.model_validate({"destination": destination, "protocol": protocol}),
        available_exits={"west"},
        geodata=GeoData(sites={"private": (GeoDomain(2, "lan"),)}, ips={"private": ((), False)}),
    )
    assert result.status == status
    assert result.action is not None and result.action.kind == action
    assert result.rule == rule


def test_subscription_preview_shows_the_effective_policy_and_all_format_omissions(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_user("person")
        for index, endpoint in enumerate(FLEETS["fleet"].endpoints):
            fleet.service.put_endpoint(f"entry-{index}", endpoint)
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        before = fleet.service._store.db.total_changes
        preview = fleet.service.preview_subscription(SubscriptionPreviewRequest(username="person", suffix="singbox"))
        assert preview.delivery.effective.policy.id == "work"
        formats = {format.format: format for format in preview.delivery.formats}
        assert set(formats) == {"xray-json", "mihomo", "stash", "singbox", "base64"}
        assert formats["singbox"].rules[1].domains == ["domain:example.test"]
        assert formats["base64"].rules == []
        assert any(omission.reason == "rules_not_carried" for omission in formats["base64"].omitted)
        assert fleet.service._store.db.total_changes == before


def test_api_and_cli_explain_inline_rules_without_any_network_lookup(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_user("person")
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        endpoint = FLEETS["fleet"].endpoints[0]
        fleet.service.put_endpoint("entry", endpoint)
        url = fleet.url + "/api/delivery-policies/explain"
        status, result = token_request(
            url, "POST", {"destination": "chat.example.test:443", "person": "person", "format": "singbox"}, ADMIN_TOKEN
        )
        assert status == 200 and result["status"] == "matched"
        assert result["action"]["kind"] == "block" and result["rule"] == 2
        assert result["destination"]["host"] == "chat.example.test"
        cluster = ClusterConfig(
            panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
        )
        try:
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                response = CliRunner().invoke(
                    app,
                    [
                        "fleet",
                        "routing",
                        "explain",
                        "chat.example.test:443",
                        "--person",
                        "person",
                        "--format",
                        "singbox",
                        "--json",
                    ],
                )
            assert response.exit_code == 0, response.output
            assert json.loads(response.stdout)["data"]["rule"] == 2
        finally:
            set_json_mode(False)


@pytest.mark.parametrize(
    "destination, action, rule",
    [
        ("chat.example.test:443", "block", 2),
        ("video.example.org:443", "direct", 4),
        ("198.51.100.10:443", "direct", 5),
        ("service.example.org:8444", "block", 6),
        ("udp://service.example.org:5000", "proxy", 7),
    ],
)
def test_explainer_preserves_first_match_order_and_matches_each_typed_kind(
    tmp_path: Path, destination: str, action: str, rule: int
) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_endpoint("entry", FLEETS["fleet"].endpoints[0])
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        status, result = token_request(
            fleet.url + "/api/delivery-policies/explain",
            "POST",
            {"destination": destination, "format": "singbox"},
            ADMIN_TOKEN,
        )
        assert status == 200
        assert result["status"] == "matched" and result["action"]["kind"] == action and result["rule"] == rule


def test_explainer_reports_missing_geodata_and_port_instead_of_guessing(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_endpoint("entry", FLEETS["fleet"].endpoints[0])
        status, unknown = token_request(
            fleet.url + "/api/delivery-policies/explain",
            "POST",
            {"destination": "example.test", "format": "xray-json"},
            ADMIN_TOKEN,
        )
        assert status == 200 and unknown["status"] == "unknown"
        assert unknown["reason"] == "geodata_unavailable"
        status, unsupported = token_request(
            fleet.url + "/api/delivery-policies/explain",
            "POST",
            {"destination": "example.test", "format": "base64"},
            ADMIN_TOKEN,
        )
        assert status == 200 and unsupported["status"] == "unavailable" and unsupported["reason"] == "rules_not_carried"


def test_connection_page_gets_format_limits_without_private_assignment_data(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        user = fleet.service.put_user("person")
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        info = json.loads(fleet.service.subscription(user.sub_token, "info").body)
        summary = info["user"]["deliveryCapabilities"]
        base64 = next(format for format in summary["formats"] if format["format"] == "base64")
        assert not base64["routing_supported"] and "rules_not_carried" in base64["omitted"]
        text = json.dumps(summary)
        assert "work" not in text and "override" not in text and "profiles" not in text


def test_lower_grants_cannot_read_private_policy_details_or_explain_a_person(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_user("person")
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        issued = fleet.service.tokens.issue(
            TokenCreate(name="Read", capabilities=["people.read", "delivery.explain"]), issuer=ROOT
        )
        status, result = token_request(
            fleet.url + "/api/subscription/preview", "POST", {"username": "person"}, issued.token
        )
        assert status == 200 and "delivery" not in result and "explanation" not in result
        assert (
            token_request(
                fleet.url + "/api/delivery-policies/explain",
                "POST",
                {"destination": "example.test", "person": "person"},
                issued.token,
            )[0]
            == 403
        )
        assert (
            token_request(
                fleet.url + "/api/subscription/preview",
                "POST",
                {"username": "person", "destination": "example.test"},
                issued.token,
            )[0]
            == 403
        )
