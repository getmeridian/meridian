"""Teardown must remove every artifact Meridian creates, across versions."""

from __future__ import annotations

import re
import subprocess

import pytest

from meridian.provision.steps import ProvisionContext
from meridian.provision.uninstall import Uninstall
from tests.support.mock_connection import MockConnection


def _commands() -> list[str]:
    conn = MockConnection()
    result = Uninstall().run(conn, ProvisionContext(ip="198.51.100.1"))
    assert result.status == "changed"
    return conn.calls


def test_removes_v4_server_artifacts() -> None:
    script = "\n".join(_commands())

    assert "/etc/nginx/stream.d/meridian-v4-*.conf" in script
    assert "/etc/nginx/sites-enabled/meridian-v4-*.conf" in script
    assert "meridian-realm-*.service" in script
    assert "netfwd-*.service" in script
    assert "/etc/netfwd" in script
    assert "/var/lib/host-baseline" in script
    assert "/var/lib/meridian" in script
    assert "ufw delete allow 443/udp" in script


def test_removes_the_control_service_and_node_agent_stopping_the_agent_first() -> None:
    commands = _commands()
    stops = [call for call in commands if call.startswith("systemctl stop ")]
    script = "\n".join(commands)

    assert [call.split()[2] for call in stops if call.split()[2].endswith(("netagent.service", "netcore.service"))] == [
        "netagent.service",
        "netcore.service",
    ]
    for path in (
        "/opt/ctld",
        "/etc/ctld",
        "/var/lib/ctld",
        "/etc/netagent",
        "/var/lib/netagent",
        "/usr/local/lib/netagent",
    ):
        assert path in script
    assert "userdel ctld" in script


def test_removes_meridian_3x_panel() -> None:
    assert "rm -rf /opt/3x-ui" in _commands()


def test_tagged_ufw_rule_numbers_under_both_comment_schemes_are_extracted_highest_first() -> None:
    command = next(call for call in _commands() if "ufw status numbered" in call)
    pipeline = command.split(" | while read")[0].replace("ufw status numbered 2>/dev/null | ", "")
    status = (
        "Status: active\n\n"
        "     To                         Action      From\n"
        "     --                         ------      ----\n"
        "[ 1] 22/tcp                     ALLOW IN    Anywhere\n"
        "[ 2] 443/tcp                    ALLOW IN    Anywhere                   # meridian-v4-0123456789abcdef\n"
        "[ 3] 3010/tcp                   ALLOW IN    198.51.100.10              # meridian-v4-fedcba9876543210\n"
        "[ 4] 8443/tcp                   ALLOW IN    Anywhere                   # my-fw-rule\n"
        "[ 7] 3011/tcp                   ALLOW IN    198.51.100.11              # fw-0123456789abcdef\n"
        "[10] 443/udp                    ALLOW IN    Anywhere                   # meridian-v4-aaaaaaaaaaaaaaaa\n"
    )
    out = subprocess.run(["sh", "-c", pipeline], input=status, capture_output=True, text=True, check=True).stdout

    assert out.split() == ["10", "7", "3", "2"]
    assert not re.search(r"\b1\b", out)


def test_legacy_proxy_cleanup_only_touches_meridian_owned_configs(tmp_path) -> None:
    from meridian.provision.nginx import LEGACY_PROXY_CLEANUP

    assert "/etc/caddy/Caddyfile" not in LEGACY_PROXY_CLEANUP
    assert "/var/lib/caddy" not in LEGACY_PROXY_CLEANUP
    # Every stop/delete sits behind a Meridian marker check.
    haproxy, caddy = LEGACY_PROXY_CLEANUP.split("if ")[1:3]
    assert haproxy.startswith("grep -qs '^# HAProxy SNI Router' /etc/haproxy/haproxy.cfg")
    assert "systemctl stop haproxy" in haproxy
    assert caddy.startswith("[ -e /etc/caddy/conf.d/meridian.caddy ]")
    assert "systemctl stop caddy" in caddy


def test_teardown_leaves_the_operators_meridian_cli_in_place(mock_conn) -> None:
    """Run on the server, teardown must not delete the command its own output says to run next."""
    from meridian.provision.steps import ProvisionContext
    from meridian.provision.uninstall import Uninstall

    Uninstall().run(mock_conn, ProvisionContext(ip="198.51.100.1"))

    mock_conn.assert_not_called_with_pattern("bin/meridian")


def test_teardown_leaves_fail2ban_running() -> None:
    """fail2ban is host hardening, like the SSH settings; it may predate Meridian (#70)."""
    assert not any("fail2ban" in call for call in _commands())


@pytest.mark.parametrize(
    ("leftover", "expected"),
    [
        ([], "stop"),
        (["conf.d/default.conf"], "stop"),
        (["sites-enabled/shop"], "reload"),
        (["conf.d/blog.conf"], "reload"),
        (["stream.d/other.conf"], "reload"),
    ],
)
def test_nginx_is_stopped_only_when_no_operator_config_remains(tmp_path, leftover, expected) -> None:
    from meridian.provision.uninstall import NGINX_STOP_UNLESS_SHARED

    for directory in ("sites-enabled", "conf.d", "stream.d"):
        (tmp_path / directory).mkdir()
    for name in leftover:
        (tmp_path / name).write_text("")
    script = NGINX_STOP_UNLESS_SHARED.replace("/etc/nginx", str(tmp_path)).replace("systemctl", "echo")
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout

    assert out.split()[0] == expected
