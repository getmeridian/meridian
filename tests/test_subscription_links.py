"""Links handed to end users must never reveal the admin panel's secret path."""

from __future__ import annotations

import re
from unittest.mock import patch

from meridian.cluster import PanelConfig
from meridian.provision.containers import ComposeDeployResult
from meridian.provision.nginx_render import render_nginx_ip_config
from meridian.provision.remnawave_panel import DeployRemnawavePanel
from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection


def test_subscription_url_uses_the_connection_page_path() -> None:
    panel = PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages")

    url = panel.subscription_url("abc/123")

    assert url == "https://198.51.100.1/pages/sub/abc%2F123"
    assert "adminsecret" not in url


def test_subscription_url_is_empty_rather_than_falling_back_to_the_admin_path() -> None:
    assert PanelConfig(url="https://198.51.100.1/adminsecret").subscription_url("abc") == ""
    assert PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages").subscription_url("") == ""


def test_nginx_exposes_only_the_panel_subscription_route_under_the_page_path() -> None:
    config = render_nginx_ip_config(
        server_ip="198.51.100.1",
        nginx_internal_port=8443,
        panel_web_base_path="adminsecret",
        panel_internal_port=3000,
        info_page_path="pages",
    )

    block = re.search(r"location /pages/sub/ \{(.*?)\}", config, re.S)
    assert block is not None
    assert "proxy_pass http://127.0.0.1:3000/api/sub/;" in block.group(1)


def test_panel_advertises_subscriptions_on_the_public_path() -> None:
    conn = MockConnection()
    conn.when("docker inspect", rc=1)
    conn.when("test -f /opt/remnawave/.env", rc=1)
    conn.when("test -d /opt/remnawave/data", rc=1)
    captured: dict[str, str] = {}

    def fake_deploy(_conn, _work_dir, _compose, *, env_files, **_kwargs):
        captured.update({env.filename: env.content for env in env_files})
        return ComposeDeployResult(changed=True)

    ctx = ProvisionContext(ip="198.51.100.1")
    ctx.web_base_path = "adminsecret"
    ctx.info_page_path = "pages"
    with patch("meridian.provision.containers.deploy_compose_stack", side_effect=fake_deploy):
        DeployRemnawavePanel().run(conn, ctx)

    assert "SUB_PUBLIC_DOMAIN=198.51.100.1/pages/sub\n" in captured[".env"]
    assert "adminsecret" not in captured[".env"]
