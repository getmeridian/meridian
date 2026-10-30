"""Individual handoff credentials share subscription order and existing access scopes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_show
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import ClientEndpoint, ServiceSettings, UserUpdate
from meridian.control.ui_routes import CONTENT_SECURITY_POLICY, SESSION_COOKIE
from meridian.core.redaction import REDACTED, redact
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent

PUBLIC_URL = "https://198.51.100.1/connect"
ENDPOINTS = [
    ClientEndpoint(
        remark="UDP fallback",
        protocol="hysteria2",
        address="198.51.100.1",
        port=443,
        position=30,
        tls_sni="vpn.example.com",
        salamander_password="test-only-obfuscation",
    ),
    ClientEndpoint(
        remark="Primary",
        protocol="reality",
        address="198.51.100.1",
        port=443,
        position=0,
        reality_sni="www.example.com",
        reality_public_key="a" * 43,
        reality_short_id="abcdef01",
        fingerprint="chrome",
    ),
    ClientEndpoint(
        remark="Primary",
        protocol="wss",
        address="vpn.example.com",
        port=443,
        position=20,
        tls_sni="vpn.example.com",
        host="vpn.example.com",
        path="connect",
    ),
    ClientEndpoint(
        remark="Alternate fingerprint",
        protocol="reality",
        address="198.51.100.1",
        port=443,
        position=1,
        reality_sni="www.example.com",
        reality_public_key="a" * 43,
        reality_short_id="abcdef01",
        fingerprint="firefox",
        fingerprint_copy=True,
    ),
    ClientEndpoint(
        remark="XHTTP",
        protocol="xhttp",
        address="vpn.example.com",
        port=443,
        position=10,
        tls_sni="vpn.example.com",
        host="vpn.example.com",
        path="connect",
    ),
]


def prepare(admin: ControlClient) -> None:
    admin.put_user("sample")
    admin.put_settings(ServiceSettings(profile_title="Test fleet", public_url=PUBLIC_URL))
    for index, endpoint in enumerate(ENDPOINTS):
        admin.put_endpoint(f"endpoint-{index}", endpoint)


def test_connections_are_the_canonical_subscription_links_and_explicit_format_urls(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        prepare(admin)
        user = admin.get_user("sample")
        reply = httpx.get(url + "/api/users/sample/connections", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert reply.status_code == 200
        view = reply.json()
        expected = base64.b64decode(admin.fetch_subscription(user.sub_token)).decode().splitlines()
        assert [connection["uri"] for connection in view["connections"]] == expected
        assert [connection["remark"] for connection in view["connections"]] == [
            unquote(urlsplit(uri).fragment) for uri in expected
        ]
        assert [connection["protocol"] for connection in view["connections"]] == [
            "reality",
            "reality",
            "xhttp",
            "wss",
            "hysteria2",
        ]
        assert all(connection["available"] for connection in view["connections"])
        assert view["profile_title"] == "Test fleet"
        assert view["formats"] == [
            {"format": name, "url": f"{PUBLIC_URL}/sub/{user.sub_token}/{name}"}
            for name in ("xray-json", "mihomo", "singbox")
        ]
        assert reply.headers["Cache-Control"] == "no-store"
        assert admin.events().events[-1].kind == "user.created"


@pytest.mark.parametrize("scope", ["read", "propose", "approve", "people", "admin"])
def test_only_scopes_that_read_subscription_credentials_can_read_individual_connections(
    scope: str, tmp_path: Path
) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        prepare(admin)
        token = admin.create_token("Test device", scope).token
        reply = httpx.get(url + "/api/users/sample/connections", headers={"Authorization": f"Bearer {token}"})
        assert reply.status_code == (200 if scope in ("people", "admin") else 403)
        if reply.status_code == 403:
            assert admin.get_user("sample").uuid not in reply.text


def test_connection_credentials_are_fully_redacted_without_exact_producing_paths() -> None:
    value = {
        "connections": [
            {
                "uri": "vless://test-only-id@198.51.100.1:443?pbk=test-only-key#Primary",
                "qr_svg": "data:image/svg+xml;base64,dGVzdC1vbmx5LXNlY3JldA==",
            }
        ]
    }
    assert redact(value)["connections"][0] == {"uri": REDACTED, "qr_svg": REDACTED}
    assert redact(value, reveal=frozenset({"connections.0.uri", "connections.0.qr_svg"})) == value


def test_client_show_connections_reveals_only_its_producing_output(
    tmp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with serve_control(tmp_home) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        prepare(admin)
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1", sub_path="connect"),
            topology_intent=single_exit_intent(),
        )
        set_json_mode(True)
        try:
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                run_show("sample", connections=True)
        finally:
            set_json_mode(False)
        output = json.loads(capsys.readouterr().out)
        connection = output["data"]["connections"]["connections"][0]
        assert admin.get_user("sample").uuid in connection["uri"]
        assert redact(output)["data"]["connections"]["connections"][0]["uri"] == REDACTED
        assert output["data"]["connections"]["formats"][0]["url"].endswith("/xray-json")


def test_client_show_documents_the_connections_option() -> None:
    reply = CliRunner().invoke(app, ["client", "show", "--help"])
    assert reply.exit_code == 0
    assert "--connections" in unstyle(reply.output)


def test_owner_share_uses_the_same_connections_and_only_local_qr_codes(tmp_path: Path) -> None:
    with (
        serve_control(tmp_path, public_path="/console") as url,
        ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin,
    ):
        prepare(admin)
        setup = httpx.post(
            url + "/auth/setup",
            headers={"X-Meridian-Request": "1"},
            json={"token": admin.issue_setup_link().token, "password": "test-only-owner-passphrase"},
        )
        cookie = setup.cookies[SESSION_COOKIE]
        headers = {"X-Meridian-Request": "1", "Cookie": f"{SESSION_COOKIE}={cookie}"}
        reply = httpx.get(url + "/ui/api/people/sample/share", headers=headers)
        assert reply.status_code == 200 and reply.headers["Cache-Control"] == "no-store"
        view = reply.json()
        expected = admin.user_connections("sample").connections
        assert [connection["uri"] for connection in view["connections"]] == [connection.uri for connection in expected]
        assert all(connection["qr_svg"].startswith("data:image/svg+xml") for connection in view["connections"])
        api_view = httpx.get(url + "/api/users/sample/connections", headers=headers)
        assert api_view.status_code == 200
        assert reply.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("status", ["disabled", "expired"])
def test_inactive_connections_show_canonical_inert_notices_without_live_credentials(
    status: str, tmp_path: Path
) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        prepare(admin)
        original = admin.get_user("sample")
        change = UserUpdate(status="disabled") if status == "disabled" else UserUpdate(expire_at="2020-01-01T00:00:00Z")
        admin.update_user("sample", change)
        view = admin.user_connections("sample")
        expected = base64.b64decode(admin.fetch_subscription(original.sub_token)).decode().splitlines()
        assert [connection.uri for connection in view.connections] == expected
        assert all(not connection.available and connection.protocol is None for connection in view.connections)
        assert original.uuid not in view.model_dump_json()
