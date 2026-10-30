"""Tests for migrate's Remnawave reader — user parsing, retry, typed errors, paging."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from meridian.remnawave import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
    RemnawaveReader,
)
from meridian.remnawave.models import parse_user


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


def _make_panel(mock_client: MagicMock | None = None) -> RemnawaveReader:
    """Create a RemnawaveReader with a mocked httpx.Client."""
    client = mock_client or MagicMock()
    panel = RemnawaveReader.__new__(RemnawaveReader)
    panel._base = "https://198.51.100.1/panel"
    panel._timeout = 30
    panel._max_retries = 3
    panel._client = client
    return panel


def _wired_panel(*responses: MagicMock) -> tuple[RemnawaveReader, MagicMock]:
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
            panel = RemnawaveReader("https://panel.example/secret/", "test-token", timeout=7.5)

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
            RemnawaveReader("https://panel.example", "Bearer test-token", verify=False)

        assert raw_client.call_args.kwargs["verify"] is False
        assert raw_client.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"


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

    def test_reads_what_migration_carries_and_reports(self) -> None:
        user = parse_user(
            {
                "lastTrafficResetAt": "2026-10-01T00:00:00.000Z",
                "hwidDeviceLimit": 3,
                "tag": "FAMILY",
                "telegramId": 1000001,
                "userTraffic": {"usedTrafficBytes": 10, "lifetimeUsedTrafficBytes": 99},
            }
        )
        assert (user.used_traffic_bytes, user.lifetime_used_traffic_bytes) == (10, 99)
        assert user.last_traffic_reset_at == "2026-10-01T00:00:00.000Z"
        assert (user.hwid_device_limit, user.tag, user.telegram_id) == (3, "FAMILY", "1000001")

    def test_id_zero_is_preserved(self) -> None:
        assert parse_user({"id": 0}).id == "0"

    def test_handles_missing_fields(self) -> None:
        user = parse_user({})
        assert user.id == ""
        assert user.username == ""
        assert user.used_traffic_bytes == 0


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
