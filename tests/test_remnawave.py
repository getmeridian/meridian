"""Tests for Remnawave API client — finders, SDK converters, retry, auth."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from remnawave.models.config_profiles import CreateConfigProfileRequestDto
from remnawave.models.internal_squads import UpdateInternalSquadRequestDto
from remnawave.models.nodes import CreateNodeRequestDto
from remnawave.models.users import CreateUserRequestDto

from meridian.remnawave import (
    ConfigProfile,
    Host,
    MeridianPanel,
    Node,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
    _host_from_sdk,
    _inbound_from_sdk,
    _node_from_sdk,
    _user_from_sdk,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = ""
    resp.content = b""
    resp.raise_for_status = MagicMock()
    return resp


def _make_panel(mock_client: MagicMock | None = None) -> MeridianPanel:
    """Create a MeridianPanel with a mocked httpx.Client and SDK bypassed."""
    client = mock_client or MagicMock()
    panel = MeridianPanel.__new__(MeridianPanel)
    panel._base = "https://198.51.100.1/panel"
    panel._token = "test-token"
    panel._timeout = 30
    panel._max_retries = 3
    panel._client = client
    panel._sdk = MagicMock()
    return panel


def _ns(**kwargs: object) -> SimpleNamespace:
    """Create a SimpleNamespace for mocking SDK response objects."""
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# Finder Methods
# ---------------------------------------------------------------------------


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

    def test_returns_none_when_not_found(self) -> None:
        panel = _make_panel()
        panel.list_nodes = MagicMock(return_value=[Node(uuid="uuid-1", address="198.51.100.1")])
        assert panel.find_node_by_address("198.51.100.99") is None

    def test_returns_none_on_empty_list(self) -> None:
        panel = _make_panel()
        panel.list_nodes = MagicMock(return_value=[])
        assert panel.find_node_by_address("198.51.100.1") is None


class TestFindHostByRemark:
    def test_returns_matching_host(self) -> None:
        panel = _make_panel()
        panel.list_hosts = MagicMock(
            return_value=[
                Host(uuid="h1", remark="reality-198.51.100.1"),
                Host(uuid="h2", remark="xhttp-198.51.100.1"),
            ]
        )
        result = panel.find_host_by_remark("xhttp-198.51.100.1")
        assert result is not None
        assert result.uuid == "h2"

    def test_returns_none_when_not_found(self) -> None:
        panel = _make_panel()
        panel.list_hosts = MagicMock(return_value=[Host(uuid="h1", remark="reality-198.51.100.1")])
        assert panel.find_host_by_remark("wss-example.com") is None

    def test_returns_none_on_empty_list(self) -> None:
        panel = _make_panel()
        panel.list_hosts = MagicMock(return_value=[])
        assert panel.find_host_by_remark("reality-198.51.100.1") is None


class TestFindConfigProfileByName:
    def test_returns_matching_profile(self) -> None:
        panel = _make_panel()
        panel.list_config_profiles = MagicMock(
            return_value=[
                ConfigProfile(uuid="cp-1", name="meridian-default"),
                ConfigProfile(uuid="cp-2", name="other"),
            ]
        )
        result = panel.find_config_profile_by_name("meridian-default")
        assert result is not None
        assert result.uuid == "cp-1"

    def test_returns_none_when_not_found(self) -> None:
        panel = _make_panel()
        panel.list_config_profiles = MagicMock(return_value=[ConfigProfile(uuid="cp-1", name="meridian-default")])
        assert panel.find_config_profile_by_name("nonexistent") is None

    def test_returns_none_on_empty_list(self) -> None:
        panel = _make_panel()
        panel.list_config_profiles = MagicMock(return_value=[])
        assert panel.find_config_profile_by_name("meridian-default") is None


# ---------------------------------------------------------------------------
# SDK → Dataclass Converters
# ---------------------------------------------------------------------------


class TestUserFromSdk:
    """Tests use snake_case field names because real SDK Pydantic models expose
    Python attributes in snake_case (camelCase is only the JSON serialization alias).
    Reading via camelCase silently returns defaults — see _user_from_sdk docstring.
    """

    def test_converts_all_fields(self) -> None:
        # Real shape: traffic counters live on the nested user_traffic sub-model;
        # built top-level user fields are short_uuid, traffic_limit_bytes, etc.
        obj = _ns(
            uuid="u-1",
            short_uuid="abc",
            username="alice",
            vless_uuid="vl-1",
            status="ACTIVE",
            traffic_limit_bytes=2048,
            created_at="2026-01-01",
            sub_revoked_at="",
            user_traffic=_ns(used_traffic_bytes=1024, online_at="2026-04-01"),
        )
        user = _user_from_sdk(obj)
        assert user.uuid == "u-1"
        assert user.short_uuid == "abc"
        assert user.username == "alice"
        assert user.status == "ACTIVE"
        assert user.used_traffic_bytes == 1024
        assert user.traffic_limit_bytes == 2048
        assert user.online_at == "2026-04-01"

    def test_handles_missing_fields(self) -> None:
        user = _user_from_sdk(_ns(uuid="u-1"))
        assert user.uuid == "u-1"
        assert user.username == ""
        assert user.used_traffic_bytes == 0

    def test_handles_none_traffic(self) -> None:
        user = _user_from_sdk(_ns(traffic_limit_bytes=None, user_traffic=None))
        assert user.used_traffic_bytes == 0
        assert user.traffic_limit_bytes == 0

    def test_reads_nested_user_traffic_fields(self) -> None:
        user = _user_from_sdk(_ns(user_traffic=_ns(used_traffic_bytes=4096, online_at="2026-04-02")))
        assert user.used_traffic_bytes == 4096
        assert user.online_at == "2026-04-02"

    def test_camelcase_attributes_silently_drop_data(self) -> None:
        # Regression: SDK 2.7.x Pydantic models do not expose camelCase aliases as
        # Python attributes. If we ever revert to getattr(..., "shortUuid", ...) the
        # field will become empty without any error.
        obj = _ns(uuid="u-1", shortUuid="WRONG_CAMEL", short_uuid="right_snake")
        user = _user_from_sdk(obj)
        assert user.short_uuid == "right_snake"


class TestUserFromRealSdkModel:
    """Round-trip parsing through actual SDK Pydantic models — catches any future
    SDK upgrade that renames fields or changes alias mapping."""

    def test_parses_real_user_response_dto(self) -> None:
        from remnawave.models.users import GetUserByUsernameResponseDto

        # JSON-shaped payload (camelCase) — what the panel returns and what
        # the SDK consumes through Field(alias=...).
        payload = {
            "uuid": "00000000-0000-0000-0000-000000000001",
            "id": 1,
            "shortUuid": "short_42",
            "username": "alice",
            "vlessUuid": "00000000-0000-0000-0000-000000000002",
            "trojanPassword": "trojanpwd_long",
            "ssPassword": "sspwd_long",
            "lastTriggeredThreshold": 0,
            "trafficLimitBytes": 5000,
            "expireAt": "2099-01-01T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-02T00:00:00Z",
            "activeInternalSquads": [],
            "userTraffic": {
                "usedTrafficBytes": 1234,
                "lifetimeUsedTrafficBytes": 9999,
                "onlineAt": "2026-04-15T10:00:00Z",
            },
            "subscriptionUrl": "https://panel/api/sub/short_42",
        }
        sdk_obj = GetUserByUsernameResponseDto.model_validate(payload)
        user = _user_from_sdk(sdk_obj)
        # All these would be defaults if we read camelCase aliases via getattr.
        assert user.short_uuid == "short_42"
        assert user.vless_uuid == "00000000-0000-0000-0000-000000000002"
        assert user.traffic_limit_bytes == 5000
        assert user.used_traffic_bytes == 1234
        assert user.online_at.startswith("2026-04-15")
        assert user.created_at.startswith("2026-01-01")


class TestNodeFromSdk:
    def test_converts_all_fields(self) -> None:
        obj = _ns(
            uuid="n-1",
            name="finland",
            address="198.51.100.1",
            port=3010,
            is_connected=True,
            is_disabled=False,
            xray_version="26.2.6",
            traffic_used_bytes=5000,
        )
        node = _node_from_sdk(obj)
        assert node.uuid == "n-1"
        assert node.name == "finland"
        assert node.address == "198.51.100.1"
        assert node.port == 3010
        assert node.is_connected is True
        assert node.traffic_used == 5000

    def test_handles_missing_fields(self) -> None:
        node = _node_from_sdk(_ns())
        assert node.uuid == ""
        assert node.is_connected is False


class TestNodeFromRealSdkModel:
    def test_parses_real_node_response_dto(self) -> None:
        from remnawave.models.nodes import NodeResponseDto

        payload = {
            "uuid": "00000000-0000-0000-0000-0000000000ab",
            "name": "finland",
            "address": "198.51.100.1",
            "port": 3010,
            "isConnected": True,
            "isDisabled": False,
            "isConnecting": False,
            "xrayVersion": "26.2.6",
            "trafficUsedBytes": 5000,
            "isTrafficTrackingActive": False,
            "trafficResetDay": 1,
            "notifyPercent": 80,
            "usersOnline": 0,
            "viewPosition": 0,
            "countryCode": "FI",
            "consumptionMultiplier": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "configProfile": {
                "activeConfigProfileUuid": "00000000-0000-0000-0000-0000000000aa",
                "activeInbounds": [],
            },
        }
        sdk_obj = NodeResponseDto.model_validate(payload)
        node = _node_from_sdk(sdk_obj)
        assert node.is_connected is True
        assert node.xray_version == "26.2.6"
        assert node.traffic_used == 5000


class TestHostFromSdk:
    def test_converts_all_fields(self) -> None:
        # Real SDK Host uses nested inbound.config_profile_inbound_uuid;
        # there is no top-level inbound_uuid attribute.
        obj = _ns(
            uuid="h-1",
            remark="reality-198.51.100.1",
            address="198.51.100.1",
            port=443,
            sni="www.google.com",
            is_disabled=False,
            inbound=_ns(config_profile_inbound_uuid="ib-1"),
        )
        host = _host_from_sdk(obj)
        assert host.uuid == "h-1"
        assert host.remark == "reality-198.51.100.1"
        assert host.sni == "www.google.com"
        assert host.inbound_uuid == "ib-1"

    def test_handles_missing_fields(self) -> None:
        host = _host_from_sdk(_ns())
        assert host.uuid == ""
        assert host.sni == ""

    def test_reads_nested_inbound_uuid(self) -> None:
        host = _host_from_sdk(_ns(inbound=_ns(config_profile_inbound_uuid="ib-2")))
        assert host.inbound_uuid == "ib-2"


class TestInboundFromSdk:
    def test_converts_all_fields(self) -> None:
        obj = _ns(uuid="ib-1", tag="vless-reality", type="vless", network="tcp", security="reality")
        ib = _inbound_from_sdk(obj)
        assert ib.uuid == "ib-1"
        assert ib.tag == "vless-reality"
        assert ib.type == "vless"
        assert ib.security == "reality"

    def test_handles_empty(self) -> None:
        ib = _inbound_from_sdk(_ns())
        assert ib.uuid == ""
        assert ib.tag == ""


# ---------------------------------------------------------------------------
# Retry Logic (raw httpx path — used by config profiles, squads)
# ---------------------------------------------------------------------------


class TestRetryLogic:
    def test_retries_on_500_error(self) -> None:
        mock_client = MagicMock()
        resp_500 = _mock_response(500, {"error": "internal"})
        resp_200 = _mock_response(200, {"response": {"ok": True}})
        mock_client.request.side_effect = [resp_500, resp_200]
        panel = _make_panel(mock_client)
        result = panel._request("GET", "/api/test")
        assert result == {"ok": True}
        assert mock_client.request.call_count == 2

    def test_no_retry_on_401(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(401)
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveError, match="401"):
            panel._request("GET", "/api/test")
        assert mock_client.request.call_count == 1

    def test_no_retry_on_403(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(403)
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveError, match="403"):
            panel._request("GET", "/api/test")
        assert mock_client.request.call_count == 1

    def test_retries_on_connect_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request.side_effect = [
            httpx.ConnectError("refused"),
            _mock_response(200, {"response": "ok"}),
        ]
        panel = _make_panel(mock_client)
        result = panel._request("GET", "/api/test")
        assert result == "ok"

    def test_retries_on_timeout(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request.side_effect = [
            httpx.ReadTimeout("slow"),
            _mock_response(200, {"response": "ok"}),
        ]
        panel = _make_panel(mock_client)
        result = panel._request("GET", "/api/test")
        assert result == "ok"

    def test_max_retries_exceeded_raises(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request.side_effect = httpx.ConnectError("down")
        panel = _make_panel(mock_client)
        panel._max_retries = 2
        with pytest.raises(RemnawaveError, match="Cannot connect"):
            panel._request("GET", "/api/test")
        assert mock_client.request.call_count == 2

    def test_unwraps_response_wrapper(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(200, {"response": {"users": [{"uuid": "u-1"}]}})
        panel = _make_panel(mock_client)
        result = panel._request("GET", "/api/users")
        assert result == {"users": [{"uuid": "u-1"}]}

    def test_passes_through_non_wrapped(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(200, [{"uuid": "u-1"}])
        panel = _make_panel(mock_client)
        result = panel._request("GET", "/api/users")
        assert result == [{"uuid": "u-1"}]


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_returns_true_on_success(self) -> None:
        panel = _make_panel()
        # Mock the SDK users call used by ping
        panel.ping = MagicMock(return_value=True)
        assert panel.ping() is True

    def test_ping_returns_false_on_error(self) -> None:
        panel = _make_panel()
        panel.ping = MagicMock(return_value=False)
        assert panel.ping() is False


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_closes_on_exit(self) -> None:
        mock_client = MagicMock()
        panel = _make_panel(mock_client)
        with panel:
            pass
        mock_client.close.assert_called_once()

    def test_usable_as_context_manager(self) -> None:
        panel = _make_panel()
        panel.ping = MagicMock(return_value=True)
        with panel:
            assert panel.ping() is True


# ---------------------------------------------------------------------------
# Auth Methods
# ---------------------------------------------------------------------------


class TestAuth:
    def test_login_returns_token(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {"accessToken": "jwt-123"}}

        with patch("httpx.post", return_value=mock_resp):
            token = MeridianPanel.login("https://198.51.100.1", "admin", "pass")

        assert token == "jwt-123"

    def test_login_fails_on_non_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RemnawaveError, match="login failed"):
                MeridianPanel.login("https://198.51.100.1", "admin", "wrong")

    def test_register_admin_returns_token(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"response": {"accessToken": "jwt-456"}}

        with patch("httpx.post", return_value=mock_resp):
            token = MeridianPanel.register_admin("https://198.51.100.1", "admin", "pass")

        assert token == "jwt-456"

    def test_register_admin_accepts_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {"token": "jwt-789"}}

        with patch("httpx.post", return_value=mock_resp):
            token = MeridianPanel.register_admin("https://198.51.100.1", "admin", "pass")

        assert token == "jwt-789"

    def test_login_no_token_in_response(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {}}

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RemnawaveError, match="no token"):
                MeridianPanel.login("https://198.51.100.1", "admin", "pass")


# ---------------------------------------------------------------------------
# API Methods (SDK-backed create_user, create_node, create_host)
# ---------------------------------------------------------------------------


class TestCreateUser:
    def test_create_user_calls_api(self) -> None:
        panel = _make_panel()
        panel._sdk.users.create_user = MagicMock(return_value="sdk-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="u-1", shortUuid="abc", username="alice")):
            user = panel.create_user("alice")
        assert user.uuid == "u-1"
        assert user.username == "alice"
        panel._sdk.users.create_user.assert_called_once()
        body = panel._sdk.users.create_user.call_args[0][0]
        assert isinstance(body, CreateUserRequestDto)
        assert body.username == "alice"
        assert str(body.expire_at) == "2099-12-31 23:59:59+00:00"

    def test_create_user_with_squad_uuids(self) -> None:
        panel = _make_panel()
        panel._sdk.users.create_user = MagicMock(return_value="sdk-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="u-1", username="bob")):
            panel.create_user(
                "bob",
                squad_uuids=[
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ],
            )
        body = panel._sdk.users.create_user.call_args[0][0]
        assert [str(uuid) for uuid in body.active_internal_squads] == [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]

    def test_create_user_with_traffic_limit(self) -> None:
        panel = _make_panel()
        panel._sdk.users.create_user = MagicMock(return_value="sdk-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="u-1", username="bob")):
            panel.create_user("bob", traffic_limit_bytes=1073741824)
        body = panel._sdk.users.create_user.call_args[0][0]
        assert body.traffic_limit_bytes == 1073741824


class TestCreateNode:
    def test_create_node_returns_credentials(self) -> None:
        panel = _make_panel()
        panel._sdk.nodes.create_node = MagicMock(return_value="create-node-coro")
        panel._sdk.keygen.generate_key = MagicMock(return_value="keygen-coro")
        with patch(
            "meridian.remnawave._sdk_call",
            side_effect=[_ns(uuid="n-1"), _ns(pub_key="secret-key-data")],
        ):
            creds = panel.create_node(
                "node-1",
                "198.51.100.1",
                3010,
                config_profile_uuid="00000000-0000-0000-0000-0000000000aa",
            )
        assert creds.uuid == "n-1"
        assert creds.secret_key == "secret-key-data"

    def test_create_node_with_config_profile(self) -> None:
        panel = _make_panel()
        panel._sdk.nodes.create_node = MagicMock(return_value="create-node-coro")
        panel._sdk.keygen.generate_key = MagicMock(return_value="keygen-coro")
        with patch(
            "meridian.remnawave._sdk_call",
            side_effect=[_ns(uuid="n-2"), _ns(pubKey="secret-key-data")],
        ):
            panel.create_node(
                "node-2",
                "198.51.100.2",
                3010,
                config_profile_uuid="00000000-0000-0000-0000-0000000000bb",
                inbound_uuids=[
                    "00000000-0000-0000-0000-0000000000c1",
                    "00000000-0000-0000-0000-0000000000c2",
                ],
            )
        body = panel._sdk.nodes.create_node.call_args[0][0]
        assert isinstance(body, CreateNodeRequestDto)
        assert str(body.config_profile.active_config_profile_uuid) == "00000000-0000-0000-0000-0000000000bb"
        assert [str(uuid) for uuid in body.config_profile.active_inbounds] == [
            "00000000-0000-0000-0000-0000000000c1",
            "00000000-0000-0000-0000-0000000000c2",
        ]


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
            security_layer="REALITY",
            is_disabled=True,
        )
        call_json = panel._post.call_args[1]["json"]
        assert call_json["sni"] == "www.google.com"
        assert call_json["fingerprint"] == "chrome"
        assert call_json["securityLayer"] == "REALITY"
        assert call_json["isDisabled"] is True


# ---------------------------------------------------------------------------
# Config Profiles (SDK path)
# ---------------------------------------------------------------------------


class TestConfigProfiles:
    def test_create_config_profile(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.create_config_profile = MagicMock(return_value="create-config-profile-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="cp-1", name="meridian-default")):
            profile = panel.create_config_profile("meridian-default", {"inbounds": []})
        assert profile.uuid == "cp-1"
        assert profile.name == "meridian-default"
        body = panel._sdk.config_profiles.create_config_profile.call_args[0][0]
        assert isinstance(body, CreateConfigProfileRequestDto)
        assert body.name == "meridian-default"

    def test_get_config_profile_found(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profile_by_uuid = MagicMock(return_value="get-config-profile-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="cp-1", name="test")):
            profile = panel.get_config_profile("cp-1")
            assert profile is not None
            assert profile.name == "test"

    def test_get_config_profile_not_found(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profile_by_uuid = MagicMock(return_value="get-config-profile-coro")
        with patch("meridian.remnawave._sdk_call", side_effect=RemnawaveNotFoundError("404")):
            assert panel.get_config_profile("nonexistent") is None

    def test_list_config_profiles(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profiles = MagicMock(return_value="list-config-profiles-coro")
        with patch(
            "meridian.remnawave._sdk_call",
            return_value=_ns(
                config_profiles=[
                    _ns(uuid="cp-1", name="default"),
                    _ns(uuid="cp-2", name="other"),
                ]
            ),
        ):
            profiles = panel.list_config_profiles()
        assert len(profiles) == 2

    def test_list_config_profiles_real_sdk_dto(self) -> None:
        # Round-trip through real SDK Pydantic model — guards against
        # camelCase regression in attribute access.
        from remnawave.models.config_profiles import GetAllConfigProfilesResponseDto

        payload = {
            "total": 2,
            "configProfiles": [
                {
                    "uuid": "00000000-0000-0000-0000-000000000aa1",
                    "name": "default",
                    "viewPosition": 0,
                    "config": {},
                    "inbounds": [],
                    "nodes": [],
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                },
                {
                    "uuid": "00000000-0000-0000-0000-000000000aa2",
                    "name": "other",
                    "viewPosition": 0,
                    "config": {},
                    "inbounds": [],
                    "nodes": [],
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                },
            ],
        }
        sdk_resp = GetAllConfigProfilesResponseDto.model_validate(payload)
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profiles = MagicMock(return_value="coro")
        with patch("meridian.remnawave._sdk_call", return_value=sdk_resp):
            profiles = panel.list_config_profiles()
        assert [p.name for p in profiles] == ["default", "other"]


# ---------------------------------------------------------------------------
# Internal Squads (SDK path)
# ---------------------------------------------------------------------------


class TestInternalSquads:
    def test_list_internal_squads(self) -> None:
        panel = _make_panel()
        panel._sdk.internal_squads.get_internal_squads = MagicMock(return_value="list-internal-squads-coro")
        mock_squad = MagicMock()
        mock_squad.model_dump.return_value = {"uuid": "sq-1", "name": "Default-Squad"}
        with patch(
            "meridian.remnawave._sdk_call",
            return_value=_ns(internalSquads=[mock_squad]),
        ):
            squads = panel.list_internal_squads()
        assert len(squads) == 1
        assert squads[0]["name"] == "Default-Squad"

    def test_assign_inbounds_to_squad_uses_sdk(self) -> None:
        panel = _make_panel()
        panel._sdk.internal_squads.update_internal_squad = MagicMock(return_value="update-internal-squad-coro")
        with patch("meridian.remnawave._sdk_call", return_value=_ns(uuid="sq-1")):
            panel.assign_inbounds_to_squad(
                "00000000-0000-0000-0000-0000000000f0",
                ["00000000-0000-0000-0000-0000000000f1"],
            )
        body = panel._sdk.internal_squads.update_internal_squad.call_args[0][0]
        assert isinstance(body, UpdateInternalSquadRequestDto)
        assert str(body.uuid) == "00000000-0000-0000-0000-0000000000f0"
        assert [str(uuid) for uuid in body.inbounds] == ["00000000-0000-0000-0000-0000000000f1"]

    def test_select_default_squad_uuid(self) -> None:
        from meridian.panel_bootstrap import select_default_squad_uuid as _select_default_squad_uuid

        assert _select_default_squad_uuid([{"uuid": "sq-1", "name": "Default-Squad"}]) == "sq-1"

    def test_select_default_squad_uuid_falls_back_to_first(self) -> None:
        """Panel v2.7+ may not have Default-Squad — fall back to first available."""
        from meridian.panel_bootstrap import select_default_squad_uuid as _select_default_squad_uuid

        assert _select_default_squad_uuid([{"uuid": "sq-1", "name": "Other"}]) == "sq-1"

    def test_select_default_squad_uuid_empty_list(self) -> None:
        from meridian.panel_bootstrap import select_default_squad_uuid as _select_default_squad_uuid

        assert _select_default_squad_uuid([]) == ""


# ---------------------------------------------------------------------------
# Error Subclasses & Safety
# ---------------------------------------------------------------------------


class TestErrorSubclasses:
    def test_not_found_is_remnawave_error(self) -> None:
        assert issubclass(RemnawaveNotFoundError, RemnawaveError)

    def test_auth_is_remnawave_error(self) -> None:
        assert issubclass(RemnawaveAuthError, RemnawaveError)

    def test_network_is_remnawave_error(self) -> None:
        assert issubclass(RemnawaveNetworkError, RemnawaveError)

    def test_404_raises_not_found(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(404)
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveNotFoundError):
            panel._request("GET", "/api/test")

    def test_401_raises_auth_error(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(401)
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveAuthError):
            panel._request("GET", "/api/test")

    def test_403_raises_auth_error(self) -> None:
        mock_client = MagicMock()
        mock_client.request.return_value = _mock_response(403)
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveAuthError):
            panel._request("GET", "/api/test")

    def test_connect_error_raises_network_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request.side_effect = httpx.ConnectError("refused")
        panel = _make_panel(mock_client)
        panel._max_retries = 1
        with pytest.raises(RemnawaveNetworkError):
            panel._request("GET", "/api/test")

    def test_timeout_raises_network_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request.side_effect = httpx.ReadTimeout("slow")
        panel = _make_panel(mock_client)
        panel._max_retries = 1
        with pytest.raises(RemnawaveNetworkError):
            panel._request("GET", "/api/test")

    def test_json_decode_error_raises_remnawave_error(self) -> None:
        mock_client = MagicMock()
        resp = _mock_response(200)
        resp.json.side_effect = ValueError("Expecting value")
        resp.content = b"<html>truncated"
        mock_client.request.return_value = resp
        panel = _make_panel(mock_client)
        with pytest.raises(RemnawaveError, match="invalid JSON"):
            panel._request("GET", "/api/test")


class TestGetMethodErrorPropagation:
    def test_get_config_profile_not_found_returns_none(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profile_by_uuid = MagicMock(return_value="get-config-profile-coro")
        with patch("meridian.remnawave._sdk_call", side_effect=RemnawaveNotFoundError("404")):
            assert panel.get_config_profile("cp-1") is None

    def test_get_config_profile_auth_error_propagates(self) -> None:
        panel = _make_panel()
        panel._sdk.config_profiles.get_config_profile_by_uuid = MagicMock(return_value="get-config-profile-coro")
        with patch("meridian.remnawave._sdk_call", side_effect=RemnawaveAuthError("expired")):
            with pytest.raises(RemnawaveAuthError):
                panel.get_config_profile("cp-1")


class TestListUsersPagination:
    """The panel rejects page sizes >1000 with a 400. Pagination logic must
    iterate until a short page is returned, not stop at the first page."""

    def test_paginates_until_partial_page(self) -> None:
        panel = _make_panel()
        panel._sdk.users.get_all_users = MagicMock(return_value="coro")

        # Build 2.5 pages worth of fake users (2500 users).
        # _sdk_call return values, one per loop iteration.
        page1 = _ns(users=[_ns(uuid=f"u-{i}", username=f"u{i}") for i in range(1000)])
        page2 = _ns(users=[_ns(uuid=f"u-{i}", username=f"u{i}") for i in range(1000, 2000)])
        page3 = _ns(users=[_ns(uuid=f"u-{i}", username=f"u{i}") for i in range(2000, 2500)])

        with patch("meridian.remnawave._sdk_call", side_effect=[page1, page2, page3]):
            users = panel.list_users()

        assert len(users) == 2500
        assert users[0].uuid == "u-0"
        assert users[2499].uuid == "u-2499"

    def test_stops_on_first_empty_page(self) -> None:
        panel = _make_panel()
        panel._sdk.users.get_all_users = MagicMock(return_value="coro")

        with patch("meridian.remnawave._sdk_call", side_effect=[_ns(users=[])]):
            users = panel.list_users()

        assert users == []

    def test_uses_max_page_size_within_panel_limit(self) -> None:
        """Panel rejects size > 1000 — we must pass exactly 1000 (or less)."""
        panel = _make_panel()
        panel._sdk.users.get_all_users = MagicMock(return_value="coro")

        with patch("meridian.remnawave._sdk_call", side_effect=[_ns(users=[])]):
            panel.list_users()
        kwargs = panel._sdk.users.get_all_users.call_args.kwargs
        assert kwargs.get("size", 0) <= 1000


class TestSdkExceptionTranslation:
    """_sdk_call must convert the official SDK's exception hierarchy into our
    RemnawaveError types. The handlers in commands/ rely on RemnawaveNotFoundError
    vs RemnawaveAuthError vs RemnawaveError to decide what to do — if SDK
    upgrades change exception types we must trip these tests, not silently
    let bare exceptions bubble up."""

    def test_remnawave_errors_pass_through_unchanged(self) -> None:
        from meridian.remnawave import _sdk_call

        original = RemnawaveAuthError("token expired")

        async def coro() -> None:
            raise original

        with pytest.raises(RemnawaveAuthError) as exc_info:
            _sdk_call(coro())
        # Identity is preserved; we did not double-wrap.
        assert exc_info.value is original

    def _api_err(self, msg: str):
        from remnawave.exceptions.general import ApiErrorResponse

        return ApiErrorResponse(message=msg)

    def test_sdk_not_found_becomes_remnawave_not_found(self) -> None:
        from remnawave.exceptions import NotFoundError

        from meridian.remnawave import _sdk_call

        err = self._api_err("user does not exist")

        async def coro() -> None:
            raise NotFoundError(404, err)

        with pytest.raises(RemnawaveNotFoundError):
            _sdk_call(coro())

    def test_sdk_unauthorized_becomes_remnawave_auth_error(self) -> None:
        from remnawave.exceptions import UnauthorizedError

        from meridian.remnawave import _sdk_call

        err = self._api_err("bad token")

        async def coro() -> None:
            raise UnauthorizedError(401, err)

        with pytest.raises(RemnawaveAuthError):
            _sdk_call(coro())

    def test_sdk_forbidden_becomes_remnawave_auth_error(self) -> None:
        from remnawave.exceptions import ForbiddenError

        from meridian.remnawave import _sdk_call

        err = self._api_err("scope insufficient")

        async def coro() -> None:
            raise ForbiddenError(403, err)

        with pytest.raises(RemnawaveAuthError):
            _sdk_call(coro())

    def test_sdk_network_error_becomes_remnawave_network_error(self) -> None:
        from remnawave.exceptions import NetworkError

        from meridian.remnawave import _sdk_call

        err = self._api_err("connection refused")

        async def coro() -> None:
            raise NetworkError(0, err)

        with pytest.raises(RemnawaveNetworkError):
            _sdk_call(coro())

    def test_sdk_httpx_timeout_becomes_remnawave_network_error(self) -> None:
        from meridian.remnawave import _sdk_call

        async def coro() -> None:
            raise httpx.ReadTimeout("slow")

        with pytest.raises(RemnawaveNetworkError) as exc_info:
            _sdk_call(coro())

        assert exc_info.value.category == "system"

    def _http_status_error(self, status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://panel.example/api")
        response = httpx.Response(status_code, request=request)
        return httpx.HTTPStatusError(f"status {status_code}", request=request, response=response)

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (401, RemnawaveAuthError),
            (403, RemnawaveAuthError),
            (404, RemnawaveNotFoundError),
            (500, RemnawaveError),
        ],
    )
    def test_sdk_httpx_status_errors_keep_semantics(
        self,
        status_code: int,
        expected: type[RemnawaveError],
    ) -> None:
        from meridian.remnawave import _sdk_call

        async def coro() -> None:
            raise self._http_status_error(status_code)

        with pytest.raises(expected) as exc_info:
            _sdk_call(coro())

        assert not isinstance(exc_info.value, RemnawaveNetworkError)

    def test_sdk_api_error_becomes_remnawave_error(self) -> None:
        from remnawave.exceptions import ApiError

        from meridian.remnawave import _sdk_call

        err = self._api_err("server exploded")

        async def coro() -> None:
            raise ApiError(500, err)

        with pytest.raises(RemnawaveError):
            _sdk_call(coro())

    def test_unknown_exception_bubbles_up_unchanged(self) -> None:
        """An unrelated exception (programmer error, attribute error, etc.)
        must not be silently wrapped — that would hide bugs."""
        from meridian.remnawave import _sdk_call

        async def coro() -> None:
            raise AttributeError("some_attr")

        with pytest.raises(AttributeError):
            _sdk_call(coro())
