"""Tests for the Remnawave REST client — finders, parsers, retry, auth, wire shapes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from meridian.remnawave import (
    MeridianPanel,
    Node,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)
from meridian.remnawave.models import parse_host, parse_inbound, parse_node, parse_user


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    payload = json_data if json_data is not None else {}
    resp.json.return_value = payload
    resp.text = ""
    resp.content = json.dumps(payload).encode()
    resp.raise_for_status = MagicMock()
    return resp


def _empty_response(status_code: int) -> MagicMock:
    resp = _mock_response(status_code)
    resp.content = b""
    resp.json.side_effect = ValueError("no body")
    return resp


def _make_panel(mock_client: MagicMock | None = None) -> MeridianPanel:
    """Create a MeridianPanel with a mocked httpx.Client."""
    client = mock_client or MagicMock()
    panel = MeridianPanel.__new__(MeridianPanel)
    panel._base = "https://198.51.100.1/panel"
    panel._token = "test-token"
    panel._timeout = 30
    panel._max_retries = 3
    panel._client = client
    return panel


def _wired_panel(*responses: MagicMock) -> tuple[MeridianPanel, MagicMock]:
    client = MagicMock()
    client.request.side_effect = list(responses)
    return _make_panel(client), client


def _call(client: MagicMock, index: int = 0) -> tuple[str, str, object]:
    call = client.request.call_args_list[index]
    return call.args[0], call.args[1], call.kwargs.get("json")


class TestClientConstruction:
    def test_enables_tls_verification_and_applies_timeout(self) -> None:
        raw_http = MagicMock()

        with patch("meridian.remnawave.client.httpx.Client", return_value=raw_http) as raw_client:
            panel = MeridianPanel("https://panel.example/secret/", "test-token", timeout=7.5)

        raw_client.assert_called_once_with(
            base_url="https://panel.example/secret/",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            timeout=7.5,
            verify=True,
        )
        assert panel._client is raw_http

    def test_explicit_tls_override_and_bearer_prefix_are_kept(self) -> None:
        with patch("meridian.remnawave.client.httpx.Client") as raw_client:
            MeridianPanel("https://panel.example", "Bearer test-token", verify=False)

        assert raw_client.call_args.kwargs["verify"] is False
        assert raw_client.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"


class TestFindNodeByAddress:
    def test_returns_matching_node(self) -> None:
        panel = _make_panel()
        panel.list_nodes = MagicMock(
            return_value=[
                Node(uuid="uuid-1", address="198.51.100.1"),
                Node(uuid="uuid-2", address="198.51.100.2"),
            ]
        )
        result = panel.find_node_by_address("198.51.100.2")
        assert result is not None
        assert result.uuid == "uuid-2"

    @pytest.mark.parametrize("nodes", [[Node(uuid="uuid-1", address="198.51.100.1")], []], ids=["other", "empty"])
    def test_returns_none_without_a_match(self, nodes: list[Node]) -> None:
        panel = _make_panel()
        panel.list_nodes = MagicMock(return_value=nodes)
        assert panel.find_node_by_address("198.51.100.99") is None


class TestParseUser:
    def test_reads_numeric_id_and_nested_traffic(self) -> None:
        user = parse_user(
            {
                "id": 42,
                "shortUuid": "short_42",
                "username": "alice",
                "vlessUuid": "00000000-0000-0000-0000-000000000002",
                "status": "ACTIVE",
                "trafficLimitBytes": 5000,
                "createdAt": "2026-01-01T00:00:00Z",
                "subscriptionUrl": "https://panel/api/sub/short_42",
                "activeInternalSquads": [{"uuid": "sq-1", "name": "Meridian-Access"}],
                "userTraffic": {"usedTrafficBytes": 1234, "onlineAt": "2026-04-15T10:00:00Z"},
            }
        )
        assert user.id == "42"
        assert user.short_uuid == "short_42"
        assert user.vless_uuid == "00000000-0000-0000-0000-000000000002"
        assert user.traffic_limit_bytes == 5000
        assert user.used_traffic_bytes == 1234
        assert user.online_at.startswith("2026-04-15")
        assert user.active_internal_squad_uuids == ["sq-1"]

    def test_id_zero_is_preserved(self) -> None:
        assert parse_user({"id": 0}).id == "0"

    def test_handles_missing_fields(self) -> None:
        user = parse_user({})
        assert user.id == ""
        assert user.username == ""
        assert user.used_traffic_bytes == 0


class TestParseNode:
    def test_reads_xray_version_from_versions_object(self) -> None:
        node = parse_node(
            {
                "uuid": "n-1",
                "name": "finland",
                "address": "198.51.100.1",
                "port": 3010,
                "isConnected": True,
                "isDisabled": False,
                "versions": {"xray": "26.7.28", "node": "3.4.2"},
                "trafficUsedBytes": 5000,
                "configProfile": {"activeConfigProfileUuid": "cp-1", "activeInbounds": [{"uuid": "ib-1"}]},
            }
        )
        assert node.uuid == "n-1"
        assert node.is_connected is True
        assert node.xray_version == "26.7.28"
        assert node.traffic_used == 5000
        assert node.active_config_profile_uuid == "cp-1"
        assert node.active_inbound_uuids == ["ib-1"]

    def test_null_versions_and_missing_fields(self) -> None:
        node = parse_node({"versions": None})
        assert node.uuid == ""
        assert node.xray_version == ""
        assert node.is_connected is False


class TestParseHostAndInbound:
    def test_host_reads_nested_inbound(self) -> None:
        host = parse_host(
            {
                "uuid": "h-1",
                "remark": "reality-198.51.100.1",
                "sni": "www.google.com",
                "securityLayer": "TLS",
                "inbound": {"configProfileUuid": "profile-1", "configProfileInboundUuid": "ib-1"},
            }
        )
        assert host.config_profile_uuid == "profile-1"
        assert host.inbound_uuid == "ib-1"
        assert host.security_layer == "TLS"

    def test_inbound_converts_all_fields(self) -> None:
        ib = parse_inbound({"uuid": "ib-1", "tag": "vless-reality", "type": "vless", "security": "reality"})
        assert (ib.uuid, ib.tag, ib.type, ib.security) == ("ib-1", "vless-reality", "vless", "reality")
        assert parse_inbound({}).uuid == ""


class TestRetryLogic:
    @pytest.fixture(autouse=True)
    def _no_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.remnawave.client.time.sleep", lambda _seconds: None)

    @pytest.mark.parametrize(
        "first",
        [_mock_response(500, {"error": "internal"}), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
        ids=["status-500", "connect-error", "read-timeout"],
    )
    def test_transient_failure_is_retried(self, first: object) -> None:
        panel, client = _wired_panel(first, _mock_response(200, {"response": "ok"}))
        assert panel._request("GET", "/api/test") == "ok"
        assert client.request.call_count == 2

    @pytest.mark.parametrize(
        ("status", "error", "match"),
        [(401, RemnawaveAuthError, "401"), (403, RemnawaveAuthError, "403"), (404, RemnawaveNotFoundError, None)],
    )
    def test_client_errors_raise_typed_errors_without_retry(self, status: int, error: type[Exception], match) -> None:
        panel, client = _wired_panel(_mock_response(status))
        with pytest.raises(error, match=match):
            panel._request("GET", "/api/test")
        assert client.request.call_count == 1

    @pytest.mark.parametrize(
        ("failure", "match"), [(httpx.ConnectError("refused"), "Cannot connect"), (httpx.ReadTimeout("slow"), None)]
    )
    def test_exhausted_retries_raise_network_error(self, failure: Exception, match: str | None) -> None:
        client = MagicMock()
        client.request.side_effect = failure
        panel = _make_panel(client)
        panel._max_retries = 2
        with pytest.raises(RemnawaveNetworkError, match=match):
            panel._request("GET", "/api/test")
        assert client.request.call_count == 2

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"response": {"users": [{"uuid": "u-1"}]}}, {"users": [{"uuid": "u-1"}]}),
            ([{"uuid": "u-1"}], [{"uuid": "u-1"}]),
        ],
        ids=["wrapped", "bare"],
    )
    def test_unwraps_the_response_envelope_only_when_present(self, payload: object, expected: object) -> None:
        panel, _ = _wired_panel(_mock_response(200, payload))
        assert panel._request("GET", "/api/users") == expected

    def test_json_decode_error_raises_remnawave_error(self) -> None:
        resp = _mock_response(200)
        resp.json.side_effect = ValueError("Expecting value")
        resp.content = b"<html>truncated"
        panel, _ = _wired_panel(resp)
        with pytest.raises(RemnawaveError, match="invalid JSON"):
            panel._request("GET", "/api/test")

    @pytest.mark.parametrize("error", [RemnawaveNotFoundError, RemnawaveAuthError, RemnawaveNetworkError])
    def test_typed_errors_are_remnawave_errors(self, error: type[Exception]) -> None:
        assert issubclass(error, RemnawaveError)


class TestEmptyBodies:
    @pytest.mark.parametrize(("method", "status_code"), [("DELETE", 202), ("DELETE", 204), ("POST", 200)])
    def test_empty_success_returns_none(self, method: str, status_code: int) -> None:
        panel, _ = _wired_panel(_empty_response(status_code))
        assert panel._request(method, "/api/test") is None


class TestAuth:
    def test_login_returns_token(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {"accessToken": "jwt-123"}}

        with patch("httpx.post", return_value=mock_resp) as post:
            token = MeridianPanel.login("https://198.51.100.1", "admin", "pass")

        assert token == "jwt-123"
        assert post.call_args.kwargs["verify"] is True

    def test_login_propagates_timeout_and_explicit_tls_override(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {"accessToken": "jwt-123"}}

        with patch("httpx.post", return_value=mock_resp) as post:
            MeridianPanel.login(
                "https://198.51.100.1",
                "admin",
                "pass",
                timeout=2.5,
                verify=False,
            )

        assert post.call_args.kwargs["timeout"] == 2.5
        assert post.call_args.kwargs["verify"] is False

    def test_login_fails_on_non_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RemnawaveError, match="login failed"):
                MeridianPanel.login("https://198.51.100.1", "admin", "wrong")

    @pytest.mark.parametrize(
        ("status", "body"), [(201, {"accessToken": "jwt-456"}), (200, {"token": "jwt-456"})], ids=["201", "200-token"]
    )
    def test_register_admin_returns_token(self, status: int, body: dict) -> None:
        mock_resp = MagicMock(status_code=status)
        mock_resp.json.return_value = {"response": body}

        with patch("httpx.post", return_value=mock_resp):
            assert MeridianPanel.register_admin("https://198.51.100.1", "admin", "pass") == "jwt-456"

    def test_login_no_token_in_response(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {}}

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RemnawaveError, match="no token"):
                MeridianPanel.login("https://198.51.100.1", "admin", "pass")


class TestUsers:
    def test_get_user_by_username_and_not_found(self) -> None:
        panel, client = _wired_panel(
            _mock_response(200, {"response": {"id": 1, "username": "alice"}}),
            _mock_response(404),
        )
        user = panel.get_user("alice")
        assert user is not None and user.id == "1"
        assert _call(client)[1] == "api/users/by-username/alice"
        assert panel.get_user("ghost") is None

    def test_get_user_by_id_uses_numeric_route(self) -> None:
        panel, client = _wired_panel(_mock_response(200, {"response": {"id": 12, "username": "alice"}}))
        user = panel.get_user_by_id("12")
        assert user is not None and user.username == "alice"
        assert _call(client)[1] == "api/users/12"

    def test_get_user_by_id_never_sends_legacy_uuid(self) -> None:
        panel, client = _wired_panel()
        assert panel.get_user_by_id("00000000-0000-0000-0000-000000000007") is None
        client.request.assert_not_called()

    def test_enable_disable_use_id_action_routes(self) -> None:
        panel, client = _wired_panel(_mock_response(200, {"response": {}}), _mock_response(200, {"response": {}}))
        panel.enable_user("5")
        panel.disable_user("5")
        assert _call(client, 0)[:2] == ("POST", "api/users/5/actions/enable")
        assert _call(client, 1)[:2] == ("POST", "api/users/5/actions/disable")

    def test_delete_user_accepts_204_and_reports_missing(self) -> None:
        panel, client = _wired_panel(_empty_response(204), _mock_response(404))
        assert panel.delete_user("5") is True
        assert _call(client)[:2] == ("DELETE", "api/users/5")
        assert panel.delete_user("6") is False


class TestListUsersPagination:
    """The panel rejects page sizes >1000. Pagination must iterate until a short page."""

    def test_paginates_until_partial_page(self) -> None:
        pages = [
            _mock_response(200, {"response": {"users": [{"id": i, "username": f"u{i}"} for i in range(lo, hi)]}})
            for lo, hi in ((0, 1000), (1000, 2000), (2000, 2500))
        ]
        panel, client = _wired_panel(*pages)
        users = panel.list_users()
        assert len(users) == 2500
        assert users[0].id == "0"
        assert users[2499].id == "2499"
        assert client.request.call_args_list[1].kwargs["params"] == {"start": 1000, "size": 1000}

    def test_stops_on_first_empty_page(self) -> None:
        panel, client = _wired_panel(_mock_response(200, {"response": {"users": [], "total": 0}}))
        assert panel.list_users() == []
        assert client.request.call_args.kwargs["params"]["size"] <= 1000


class TestNodes:
    def test_create_node_returns_credentials_from_secret_key(self) -> None:
        panel, client = _wired_panel(
            _mock_response(201, {"response": {"uuid": "n-1", "name": "node-1"}}),
            _mock_response(200, {"response": {"secretKey": "secret-key-data"}}),
        )
        creds = panel.create_node(
            "node-1",
            "198.51.100.1",
            3010,
            config_profile_uuid="cp-1",
            inbound_uuids=["ib-1", "ib-2"],
        )
        assert creds.uuid == "n-1"
        assert creds.secret_key == "secret-key-data"
        method, path, body = _call(client)
        assert (method, path) == ("POST", "api/nodes")
        assert body["configProfile"] == {"activeConfigProfileUuid": "cp-1", "activeInbounds": ["ib-1", "ib-2"]}
        assert body["countryCode"] == "XX"
        assert _call(client, 1)[:2] == ("GET", "api/keygen")

    def test_keygen_accepts_legacy_pub_key(self) -> None:
        panel, _ = _wired_panel(_mock_response(200, {"response": {"pubKey": "legacy"}}))
        assert panel.get_node_secret_key() == "legacy"

    def test_x25519_keypairs_come_from_the_tools_controller(self) -> None:
        keypairs = {"keypairs": [{"privateKey": "priv", "publicKey": "pub"}, "junk"]}
        panel, client = _wired_panel(_mock_response(200, {"response": keypairs}))
        assert panel.generate_x25519_keypairs() == [("priv", "pub")]
        assert _call(client)[:2] == ("GET", "api/system/tools/x25519/generate")

    def test_list_nodes_reads_bare_array(self) -> None:
        panel, _ = _wired_panel(_mock_response(200, {"response": [{"uuid": "n-1"}, {"uuid": "n-2"}]}))
        assert [node.uuid for node in panel.list_nodes()] == ["n-1", "n-2"]

    def test_delete_node_accepts_204(self) -> None:
        panel, client = _wired_panel(_empty_response(204))
        panel.delete_node("n-1")
        assert _call(client)[:2] == ("DELETE", "api/nodes/n-1")


class TestHostActions:
    def test_delete_host_accepts_204(self) -> None:
        panel, client = _wired_panel(_empty_response(204))
        panel.delete_host("h-1")
        assert _call(client)[:2] == ("DELETE", "api/hosts/h-1")

    def test_set_host_positions_sends_only_the_listed_hosts(self) -> None:
        panel, client = _wired_panel(_mock_response(200, {"response": {"isUpdated": True}}))
        panel.set_host_positions({"h-2": 3})
        assert _call(client)[:2] == ("POST", "api/hosts/actions/reorder")
        assert _call(client)[2] == {"hosts": [{"uuid": "h-2", "viewPosition": 3}]}

    def test_list_hosts_reads_bare_array(self) -> None:
        panel, _ = _wired_panel(_mock_response(200, {"response": [{"uuid": "h-1", "remark": "r"}]}))
        assert [host.remark for host in panel.list_hosts()] == ["r"]


class TestCreateHost:
    def test_create_host_minimal(self) -> None:
        panel = _make_panel()
        panel._post = MagicMock(
            return_value={
                "uuid": "h-1",
                "remark": "reality-198.51.100.1",
                "port": 443,
                "inboundUuid": "00000000-0000-0000-0000-0000000000d1",
            }
        )
        host = panel.create_host(
            remark="reality-198.51.100.1",
            address="198.51.100.1",
            port=443,
            config_profile_uuid="00000000-0000-0000-0000-0000000000d0",
            inbound_uuid="00000000-0000-0000-0000-0000000000d1",
        )
        assert host.uuid == "h-1"
        call_json = panel._post.call_args[1]["json"]
        assert call_json["inbound"]["configProfileUuid"] == "00000000-0000-0000-0000-0000000000d0"
        assert call_json["inbound"]["configProfileInboundUuid"] == "00000000-0000-0000-0000-0000000000d1"

    def test_create_host_with_optional_fields(self) -> None:
        panel = _make_panel()
        panel._post = MagicMock(return_value={"uuid": "h-2"})
        panel.create_host(
            remark="reality-198.51.100.1",
            address="198.51.100.1",
            port=443,
            config_profile_uuid="00000000-0000-0000-0000-0000000000e0",
            inbound_uuid="00000000-0000-0000-0000-0000000000e1",
            sni="www.google.com",
            fingerprint="chrome",
            security_layer="TLS",
            is_disabled=True,
            tags=["MERIDIAN_V4_XRAY_EDGE"],
            is_hidden=True,
            xray_json_template_uuid="00000000-0000-0000-0000-0000000000e2",
            exclude_from_subscription_types=["MIHOMO", "XRAY_BASE64"],
        )
        call_json = panel._post.call_args[1]["json"]
        assert call_json["sni"] == "www.google.com"
        assert call_json["fingerprint"] == "chrome"
        assert call_json["securityLayer"] == "TLS"
        assert call_json["isDisabled"] is True
        assert call_json["tags"] == ["MERIDIAN_V4_XRAY_EDGE"]
        assert call_json["isHidden"] is True
        assert call_json["xrayJsonTemplateUuid"] == "00000000-0000-0000-0000-0000000000e2"
        assert call_json["excludeFromSubscriptionTypes"] == [
            "MIHOMO",
            "XRAY_BASE64",
        ]

    def test_update_host_serializes_complete_managed_shape(self) -> None:
        panel = _make_panel()
        panel._patch = MagicMock(
            return_value={
                "uuid": "h-2",
                "remark": "xhttp-vpn.example.com",
                "address": "vpn.example.com",
                "port": 443,
                "path": "/connect",
                "sni": "vpn.example.com",
                "host": "vpn.example.com",
                "securityLayer": "TLS",
                "inbound": {
                    "configProfileUuid": "00000000-0000-0000-0000-0000000000e0",
                    "configProfileInboundUuid": "00000000-0000-0000-0000-0000000000e1",
                },
            }
        )

        host = panel.update_host(
            "h-2",
            remark="xhttp-vpn.example.com",
            address="vpn.example.com",
            port=443,
            config_profile_uuid="00000000-0000-0000-0000-0000000000e0",
            inbound_uuid="00000000-0000-0000-0000-0000000000e1",
            sni="vpn.example.com",
            host_header="vpn.example.com",
            path="/connect",
            security_layer="TLS",
        )

        body = panel._patch.call_args.kwargs["json"]
        assert body["uuid"] == "h-2"
        assert body["path"] == "/connect"
        assert body["sni"] == "vpn.example.com"
        assert body["host"] == "vpn.example.com"
        assert body["securityLayer"] == "TLS"
        assert host.config_profile_uuid == "00000000-0000-0000-0000-0000000000e0"
        assert host.inbound_uuid == "00000000-0000-0000-0000-0000000000e1"


class TestConfigProfiles:
    def test_create_config_profile(self) -> None:
        panel, client = _wired_panel(_mock_response(201, {"response": {"uuid": "cp-1", "name": "meridian-default"}}))
        profile = panel.create_config_profile("meridian-default", {"inbounds": []})
        assert (profile.uuid, profile.name) == ("cp-1", "meridian-default")
        assert _call(client) == (
            "POST",
            "api/config-profiles",
            {"name": "meridian-default", "config": {"inbounds": []}},
        )

    def test_get_config_profile_found_and_not_found(self) -> None:
        panel, _ = _wired_panel(
            _mock_response(200, {"response": {"uuid": "cp-1", "name": "test"}}), _mock_response(404)
        )
        profile = panel.get_config_profile("cp-1")
        assert profile is not None and profile.name == "test"
        assert panel.get_config_profile("nonexistent") is None

    def test_get_config_profile_auth_error_propagates(self) -> None:
        panel, _ = _wired_panel(_mock_response(401))
        with pytest.raises(RemnawaveAuthError):
            panel.get_config_profile("cp-1")

    def test_list_config_profiles(self) -> None:
        panel, _ = _wired_panel(
            _mock_response(
                200,
                {"response": {"total": 2, "configProfiles": [{"uuid": "cp-1", "name": "default"}, {"name": "other"}]}},
            )
        )
        assert [p.name for p in panel.list_config_profiles()] == ["default", "other"]


class TestInternalSquads:
    def test_list_internal_squads(self) -> None:
        panel = _make_panel()
        panel._request = MagicMock(
            return_value={
                "internalSquads": [
                    {
                        "uuid": "sq-1",
                        "name": "Default-Squad",
                        "inbounds": [{"uuid": "ib-1"}],
                        "info": {"membersCount": 2},
                    }
                ]
            }
        )

        squads = panel.list_internal_squads()

        assert len(squads) == 1
        assert squads[0].name == "Default-Squad"
        assert squads[0].inbound_uuids == ["ib-1"]
        assert squads[0].members_count == 2

    def test_update_internal_squad_inbounds_uses_typed_rest_shape(self) -> None:
        panel = _make_panel()
        panel._request = MagicMock(return_value={"uuid": "sq-1", "name": "Default-Squad"})

        panel.update_internal_squad("sq-1", inbound_uuids=["ib-1"])

        panel._request.assert_called_once_with(
            "PATCH",
            "/api/internal-squads",
            json={"uuid": "sq-1", "inbounds": ["ib-1"]},
        )
