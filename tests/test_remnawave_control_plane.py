"""Contract tests for Meridian's typed Remnawave control-plane boundary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from meridian.remnawave import MeridianPanel
from meridian.remnawave.models import (
    parse_config_profile,
    parse_host,
    parse_internal_squad,
    parse_node,
    parse_subscription_settings,
    parse_user,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "remnawave" / "2.8.0.json"


def _panel() -> MeridianPanel:
    panel = MeridianPanel.__new__(MeridianPanel)
    panel._base = "https://panel.example.com/secret"
    panel._token = "test-token"
    panel._timeout = 30
    panel._max_retries = 1
    panel._client = MagicMock()
    panel._sdk = MagicMock()
    panel._request = MagicMock()
    return panel


def test_current_tuple_fixture_round_trips_stable_models() -> None:
    payload = json.loads(_FIXTURE.read_text())

    profile = parse_config_profile(payload["config_profile"])
    node = parse_node(payload["node"])
    host = parse_host(payload["host"])
    squad = parse_internal_squad(payload["internal_squad"])
    user = parse_user(payload["user"])
    settings = parse_subscription_settings(payload["subscription_settings"])

    assert profile.inbounds[0].profile_uuid == profile.uuid
    assert node.active_config_profile_uuid == profile.uuid
    assert node.active_inbound_uuids == [profile.inbounds[0].uuid]
    assert host.config_profile_uuid == profile.uuid
    assert host.inbound_uuid == profile.inbounds[0].uuid
    assert squad.inbound_uuids == [profile.inbounds[0].uuid]
    assert user.active_internal_squad_uuids == [squad.uuid]
    assert user.subscription_url.endswith("/api/sub/service-short")
    assert settings.response_rules == {
        "rules": [{"enabled": True, "name": "keep-unmanaged"}],
        "version": "1",
    }


def test_profile_update_and_delete_use_exact_rest_shapes() -> None:
    panel = _panel()
    panel._request.side_effect = [
        {
            "uuid": "profile-1",
            "name": "exit-a",
            "config": {"inbounds": []},
            "inbounds": [],
        },
        {"isDeleted": True},
    ]

    profile = panel.update_config_profile(
        "profile-1",
        name="exit-a",
        config={"inbounds": []},
    )
    deleted = panel.delete_config_profile("profile-1")

    assert profile.uuid == "profile-1"
    assert deleted is True
    assert panel._request.call_args_list[0].args == ("PATCH", "/api/config-profiles")
    assert panel._request.call_args_list[0].kwargs["json"] == {
        "uuid": "profile-1",
        "name": "exit-a",
        "config": {"inbounds": []},
    }
    assert panel._request.call_args_list[1].args == (
        "DELETE",
        "/api/config-profiles/profile-1",
    )


def test_node_binding_serializes_profile_and_active_inbounds() -> None:
    panel = _panel()
    panel._request.return_value = {
        "uuid": "node-1",
        "configProfile": {
            "activeConfigProfileUuid": "profile-1",
            "activeInbounds": [{"uuid": "inbound-1"}],
        },
    }

    node = panel.bind_node_profile("node-1", "profile-1", ["inbound-1"])

    assert node.active_config_profile_uuid == "profile-1"
    panel._request.assert_called_once_with(
        "PATCH",
        "/api/nodes",
        json={
            "uuid": "node-1",
            "configProfile": {
                "activeConfigProfileUuid": "profile-1",
                "activeInbounds": ["inbound-1"],
            },
        },
    )


def test_external_squad_update_uses_template_wire_aliases() -> None:
    panel = _panel()
    panel._request.return_value = {
        "uuid": "external-1",
        "name": "Meridian Delivery",
        "templates": [{"templateUuid": "template-1", "templateType": "XRAY_JSON"}],
    }

    squad = panel.update_external_squad(
        "external-1",
        templates=[
            {
                "template_uuid": "template-1",
                "template_type": "XRAY_JSON",
            }
        ],
        response_headers={"X-Meridian": "managed"},
    )

    assert squad.templates == [{"template_uuid": "template-1", "template_type": "XRAY_JSON"}]
    assert panel._request.call_args.kwargs["json"] == {
        "uuid": "external-1",
        "templates": [{"templateUuid": "template-1", "templateType": "XRAY_JSON"}],
        "responseHeaders": {"X-Meridian": "managed"},
    }


def test_service_user_creation_is_explicitly_tagged_and_scoped() -> None:
    panel = _panel()
    panel._request.return_value = {
        "uuid": "user-1",
        "username": "meridian_bridge_a",
        "activeInternalSquads": [{"uuid": "squad-1"}],
        "userTraffic": {},
    }

    user = panel.create_service_user(
        "meridian_bridge_a",
        squad_uuids=["squad-1"],
        external_squad_uuid="external-1",
    )

    assert user.active_internal_squad_uuids == ["squad-1"]
    body = panel._request.call_args.kwargs["json"]
    assert body["tag"] == "MERIDIAN_SERVICE"
    assert body["activeInternalSquads"] == ["squad-1"]
    assert body["externalSquadUuid"] == "external-1"


def test_template_crud_keeps_content_typed() -> None:
    panel = _panel()
    panel._request.side_effect = [
        {
            "uuid": "template-1",
            "name": "Meridian Xray",
            "templateType": "XRAY_JSON",
            "templateJson": None,
        },
        {
            "uuid": "template-1",
            "name": "Meridian Xray",
            "templateType": "XRAY_JSON",
            "templateJson": {"outbounds": []},
        },
        {"isDeleted": True},
    ]

    created = panel.create_subscription_template("Meridian Xray", "XRAY_JSON")
    updated = panel.update_subscription_template(
        created.uuid,
        template_json={"outbounds": []},
    )
    deleted = panel.delete_subscription_template(created.uuid)

    assert updated.template_json == {"outbounds": []}
    assert deleted is True
    assert panel._request.call_args_list[1].kwargs["json"] == {
        "uuid": "template-1",
        "templateJson": {"outbounds": []},
    }


def test_settings_update_preserves_response_rules_when_unmanaged() -> None:
    panel = _panel()
    panel._request.return_value = {
        "uuid": "settings-1",
        "profileTitle": "Family VPN",
        "profileUpdateInterval": 12,
        "responseRules": {
            "version": "1",
            "rules": [{"name": "unmanaged", "enabled": True}],
        },
    }

    settings = panel.update_subscription_settings(
        "settings-1",
        profile_title="Family VPN",
        randomize_hosts=False,
    )

    body = panel._request.call_args.kwargs["json"]
    assert "responseRules" not in body
    assert body == {
        "uuid": "settings-1",
        "profileTitle": "Family VPN",
        "randomizeHosts": False,
    }
    assert settings.response_rules is not None
    assert settings.response_rules["rules"][0]["name"] == "unmanaged"


def test_fetch_subscription_returns_canonical_content_and_type() -> None:
    panel = _panel()
    response = MagicMock()
    response.status_code = 200
    response.text = "dmxlc3M6Ly8="
    response.headers = {"content-type": "text/plain; charset=utf-8"}
    panel._client.request.return_value = response

    document = panel.fetch_subscription("short id", client_type="xray-json")

    panel._client.request.assert_called_once_with(
        "GET",
        "api/sub/short%20id/xray-json",
    )
    assert document.url == ("https://panel.example.com/secret/api/sub/short%20id/xray-json")
    assert document.content == "dmxlc3M6Ly8="
    assert document.client_type == "xray-json"
    assert document.content_type.startswith("text/plain")
