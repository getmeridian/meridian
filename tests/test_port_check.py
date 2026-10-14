"""Port preflight (443, 80, nginx's internal 8443/8444): Meridian's own listeners pass, anything else conflicts."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.port_check import check_ports


def _ss(stdout: str = "", rc: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")


def _listener(process: str, port: int = 443) -> SimpleNamespace:
    return _ss(f'LISTEN 0 128 *:{port} *:* users:(("{process}",pid=1234,fd=6))\n')


@pytest.mark.parametrize(
    "ss_output",
    [
        _ss(""),
        _listener("nginx"),
        _listener("xray"),
        _listener("docker-proxy"),
        _listener("remnawave-node", port=80),
    ],
    ids=["free", "nginx", "xray", "docker-proxy", "remnawave-node"],
)
def test_free_ports_and_meridian_listeners_pass(ss_output: SimpleNamespace) -> None:
    conn = MagicMock()
    conn.run.return_value = ss_output
    check_ports(conn, yes=True)


def test_caddy_from_an_older_meridian_release_is_allowed() -> None:
    conn = MagicMock()
    conn.run.side_effect = [
        _listener("caddy"),
        _ss(),  # test -e /etc/caddy/conf.d/meridian.caddy
        _ss(),  # port 80 free
        _ss(),  # 8443 free
        _ss(),  # 8444 free
    ]
    check_ports(conn, yes=True)
    assert "meridian.caddy" in conn.run.call_args_list[1].args[0]


@pytest.fixture
def fail():
    with patch("meridian.commands.port_check.err_console"), patch("meridian.commands.port_check.fail") as fail:
        fail.side_effect = typer.Exit(2)
        yield fail


@pytest.mark.parametrize(
    ("ss_output", "named"),
    [
        (_listener("apache2"), "apache2"),
        # An unparseable users field becomes "unknown", which is never allowed.
        (_ss("LISTEN 0 128 *:443 *:* users:((broken-format))\n"), ""),
    ],
    ids=["foreign-process", "unparseable-process"],
)
def test_foreign_listener_fails_in_yes_mode(fail: MagicMock, ss_output: SimpleNamespace, named: str) -> None:
    conn = MagicMock()
    conn.run.return_value = ss_output
    with pytest.raises(typer.Exit):
        check_ports(conn, yes=True)
    fail.assert_called_once()
    assert named in fail.call_args[0][0]


@pytest.mark.parametrize("port", [8443, 8444])
def test_a_foreign_listener_on_an_internal_nginx_port_fails_before_deploy(fail: MagicMock, port: int) -> None:
    conn = MagicMock()
    conn.run.side_effect = lambda command, **_: _listener("python3", port) if f":{port}" in command else _ss()
    with pytest.raises(typer.Exit):
        check_ports(conn, yes=True)
    message = fail.call_args[0][0]
    assert f"Port {port}" in message and "python3" in message and "127.0.0.1" in message


def test_operators_own_caddy_is_a_conflict(fail: MagicMock) -> None:
    conn = MagicMock()
    conn.run.side_effect = [
        _listener("caddy"),
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
    ]
    with pytest.raises(typer.Exit):
        check_ports(conn, yes=True)
    assert "caddy" in fail.call_args[0][0]


@patch("meridian.commands.port_check.choose", return_value=2)
def test_interactive_abort_on_a_conflict_fails(_choose: MagicMock, fail: MagicMock) -> None:
    conn = MagicMock()
    conn.run.return_value = _listener("apache2", port=80)
    with pytest.raises(typer.Exit):
        check_ports(conn, yes=False)
    fail.assert_called_once()


@patch("meridian.commands.port_check.err_console")
@patch("meridian.commands.port_check.choose", return_value=1)
def test_interactive_retry_passes_once_the_port_is_free(choose: MagicMock, _console: MagicMock) -> None:
    conn = MagicMock()
    conn.run.side_effect = [_listener("apache2"), _ss(), _ss(), _ss(), _ss()]
    check_ports(conn, yes=False)
    choose.assert_called_once()
