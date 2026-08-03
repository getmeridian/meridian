"""Focused contracts for node deployment helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from meridian.node_deploy import create_api_token, panel_base_url, wait_for_node_connected


def test_create_api_token_uses_remnawave_v28_request_schema() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"response": {"token": "provisioner-token"}}

    with patch("httpx.post", return_value=response) as post:
        token = create_api_token("https://198.51.100.10/secret/", "admin-token")

    assert token == "provisioner-token"
    post.assert_called_once_with(
        "https://198.51.100.10/secret/api/tokens",
        json={
            "name": "meridian-provisioner",
            "expiresInDays": 100_000,
            "scopes": ["*"],
        },
        headers={
            "Authorization": "Bearer admin-token",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=True,
    )


def test_panel_base_url_brackets_ipv6_literals() -> None:
    assert panel_base_url("2001:0DB8:0:0:0:0:0:11", "", "secret") == ("https://[2001:0DB8:0:0:0:0:0:11]/secret/")


@patch("meridian.health.time.sleep")
def test_wait_for_node_connected_polls_panel_state(_sleep: MagicMock) -> None:
    panel = MagicMock()
    panel.get_node.side_effect = [
        None,
        SimpleNamespace(is_connected=False),
        SimpleNamespace(is_connected=True),
    ]

    assert wait_for_node_connected(panel, "node-uuid", timeout=1, interval=0.01)
    assert panel.get_node.call_count == 3
