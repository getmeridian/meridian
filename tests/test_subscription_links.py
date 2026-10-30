"""Links handed to end users must never reveal the admin panel's secret path."""

from __future__ import annotations

import re

from meridian.cluster import PanelConfig
from meridian.provision.nginx_render import render_nginx_ip_config


def test_subscription_url_uses_the_connection_page_path() -> None:
    panel = PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages")

    url = panel.subscription_url("abc/123")

    assert url == "https://198.51.100.1/pages/sub/abc%2F123"
    assert "adminsecret" not in url


def test_subscription_url_is_empty_rather_than_falling_back_to_the_admin_path() -> None:
    assert PanelConfig(url="https://198.51.100.1/adminsecret").subscription_url("abc") == ""
    assert PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages").subscription_url("") == ""


def test_nginx_exposes_only_the_service_subscription_route_under_the_page_path() -> None:
    config = render_nginx_ip_config(
        server_ip="198.51.100.1",
        nginx_internal_port=8443,
        service_base_path="adminsecret",
        service_port=3000,
        info_page_path="pages",
    )

    block = re.search(r"location /pages/sub/ \{(.*?)\}", config, re.S)
    assert block is not None
    assert "proxy_pass http://127.0.0.1:3000/sub/;" in block.group(1)
