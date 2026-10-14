"""`meridian logs` reads what a server's roles run, quotes the search text, and redacts every line."""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig, SubscriptionPageConfig
from meridian.commands import logs
from meridian.diagnostics.logs import NGINX_ERROR_LOG, LogSource, collect_log, tail_command
from meridian.host_runtime import NODE, SUBSCRIPTION_PAGE
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import add_relay_to_intent
from meridian.setup.single_server import single_server_intent
from tests.support.mock_connection import MockConnection

SECRET_PATH = "a1b2c3d4e5f6secretpanel"
SUB_PATH = "f6e5d4c3b2a1pagepath"
API_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXJpZGlhbiJ9.c2lnbmF0dXJlLXZhbHVl"


@pytest.fixture
def fleet(servers_file: Path) -> SimpleNamespace:
    """One control+exit server, one relay into it, and one saved server with no role."""
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="exit-a"))
    registry.add(ServerEntry(host="198.51.100.20", name="relay-a"))
    registry.add(ServerEntry(host="198.51.100.30", name="spare"))
    intent = single_server_intent(None, server_ref=registry.find("exit-a").id, sni="www.example.com")
    intent = add_relay_to_intent(
        intent, server_ref=registry.find("relay-a").id, title="relay-a", exit_ref="main", listen_port=8443
    )
    ClusterConfig(
        panel=PanelConfig(
            url=f"https://198.51.100.10/{SECRET_PATH}",
            api_token=API_TOKEN,
            secret_path=SECRET_PATH,
            sub_path=SUB_PATH,
        ),
        topology_intent=intent,
        subscription_page=SubscriptionPageConfig(path=SUB_PATH, deployed=True),
    ).save()
    return SimpleNamespace(conn=MockConnection())


def _run(fleet: SimpleNamespace, target: str, **kwargs: object) -> None:
    options: dict[str, object] = {"lines": 100, **kwargs}
    with patch.object(logs, "ensure_server_connection", side_effect=lambda resolved: SimpleNamespace(conn=fleet.conn)):
        logs.run(target, **options)  # type: ignore[arg-type]


def _headers(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("==> ")]


def test_control_and_exit_server_reads_node_panel_and_nginx_logs(
    fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(fleet, "exit-a")

    headers = _headers(capsys.readouterr().out)
    assert {header.split(":")[0] for header in headers} == {"==> node", "==> panel", "==> nginx"}
    assert any(SUBSCRIPTION_PAGE.name in header for header in headers)
    assert any(f"docker logs --tail 100 {shlex.quote(NODE.name)}" in call for call in fleet.conn._calls)
    assert f"tail -n 100 {NGINX_ERROR_LOG}" in fleet.conn._calls[-1]


def test_relay_server_reads_only_its_realm_units(fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    _run(fleet, "relay-a")

    assert [header.split(":")[0] for header in _headers(capsys.readouterr().out)] == ["==> relay"]
    (command,) = fleet.conn._calls
    assert "journalctl" in command and "-u netfwd-" in command and "-u meridian-realm-" in command
    assert "docker" not in command


def test_component_filter_limits_sources(fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    _run(fleet, "exit-a", components=["nginx"])

    assert _headers(capsys.readouterr().out) == [f"==> nginx: file {NGINX_ERROR_LOG} <=="]
    assert len(fleet.conn._calls) == 1


@pytest.mark.parametrize(
    ("target", "kwargs"),
    [
        ("spare", {}),
        ("exit-a", {"components": ["relay"]}),
        ("exit-a", {"components": ["xray"]}),
        ("exit-a", {"pattern": "one\ntwo"}),
    ],
    ids=["server-without-role", "component-not-on-server", "unknown-component", "multi-line-pattern"],
)
def test_refusals_happen_before_dialing(fleet: SimpleNamespace, target: str, kwargs: dict[str, object]) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, target, **kwargs)

    assert exc_info.value.exit_code == 2
    assert fleet.conn._calls == []


def test_grep_pattern_is_one_quoted_fixed_string() -> None:
    pattern = "it's; rm -rf / #"
    source = LogSource("node", "container", NODE.name)

    command = tail_command(source, lines=50, pattern=pattern)

    assert f"grep -F -i -e {shlex.quote(pattern)} | tail -n 50" in command
    assert shlex.split(command.split("grep -F -i -e ", 1)[1])[0] == pattern


def test_pattern_reaches_every_source(fleet: SimpleNamespace) -> None:
    _run(fleet, "exit-a", pattern="-handshake failed")

    assert fleet.conn._calls
    assert all(f"grep -F -i -e {shlex.quote('-handshake failed')}" in call for call in fleet.conn._calls)


def test_log_lines_are_redacted_and_printed_without_markup(
    fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet.conn.when(
        "tail -n 100",
        stdout=(
            f'2026/10/14 [error] open() failed, request: "GET /{SECRET_PATH}/api/users HTTP/2.0"\n'
            f"Authorization: Bearer {API_TOKEN}\n"
            f"GET /{SUB_PATH}/sub/Xk3PqR8sTu HTTP/1.1\n"
            "node 550e8400-e29b-41d4-a716-446655440000 \x1b[31mconnected\x1b[0m [bold]as is[/bold]\n"
        ),
    )

    _run(fleet, "exit-a", components=["nginx"])

    out = capsys.readouterr().out
    for secret in (SECRET_PATH, SUB_PATH, API_TOKEN, "Xk3PqR8sTu", "550e8400", "\x1b"):
        assert secret not in out
    assert "/[REDACTED]/api/users" in out
    assert "connected [bold]as is[/bold]" in out


def test_unreadable_log_is_shown_and_exits_3(fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    fleet.conn.when(f"docker inspect --type container {NODE.name}", rc=10)

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a")

    assert exc_info.value.exit_code == 3
    assert "(unavailable: container not found)" in capsys.readouterr().out


def test_journal_placeholder_and_blank_lines_are_dropped() -> None:
    conn = MockConnection().when("journalctl", stdout="-- No entries --\n\n")

    tail = collect_log(conn, LogSource("relay", "unit", "netfwd-abc"), lines=10)

    assert tail.available and tail.lines == ()


def test_cli_passes_flags_through() -> None:
    with patch("meridian.commands.logs.run") as run:
        result = CliRunner().invoke(app, ["logs", "exit-a", "-n", "20", "-g", "timeout", "-c", "node", "-c", "nginx"])

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(
        "exit-a", user="", ssh_port=0, lines=20, pattern="timeout", components=["node", "nginx"]
    )
