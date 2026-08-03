"""Fail-closed, privacy-preserving preflight behavior."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.check import run
from meridian.ssh import CommandResult


class FakeConnection:
    def __init__(
        self,
        *,
        sni_reachable: bool,
        ss_returncode: int = 0,
        ss_stdout: str = "",
        meridian_managed: bool = False,
        dns_returncode: int = 0,
        sni_returncode: int = 0,
        ownership_returncode: int = 0,
        clock_available: bool = True,
    ) -> None:
        self.sni_reachable = sni_reachable
        self.ss_returncode = ss_returncode
        self.ss_stdout = ss_stdout
        self.meridian_managed = meridian_managed
        self.dns_returncode = dns_returncode
        self.sni_returncode = sni_returncode
        self.ownership_returncode = ownership_returncode
        self.clock_available = clock_available
        self.commands: list[str] = []

    def run(self, command: str, **_kwargs: object) -> CommandResult:
        self.commands.append(command)
        stdout = ""
        if "openssl s_client" in command and self.sni_reachable:
            stdout = "CONNECTED\n"
        elif "getent ahostsv4" in command and self.dns_returncode == 0:
            stdout = "203.0.113.20\n"
        elif command.startswith("ss -H -tlnp"):
            stdout = self.ss_stdout
        elif command.startswith("test -f /etc/meridian/node.yml") and self.meridian_managed:
            stdout = "managed\n"
        elif "nohup python3 -c" in command:
            stdout = "4242\n"
        elif command == "date +%s" and self.clock_available:
            stdout = f"{int(time.time())}\n"
        if command.startswith("ss -H -tlnp"):
            returncode = self.ss_returncode
        elif "openssl s_client" in command or "nc -z" in command:
            returncode = self.sni_returncode
        elif command.startswith("test -f /etc/meridian/node.yml"):
            returncode = self.ownership_returncode
        elif "getent " in command:
            returncode = self.dns_returncode
        else:
            returncode = 0
        return CommandResult(args=command, returncode=returncode, stdout=stdout)


def _run_preflight(
    connection: FakeConnection,
    *,
    os_info: str = "Ubuntu 24.04 LTS",
    disk_mb: int | None = 4096,
    external_reachable: bool = True,
    local_mode: bool = False,
    address: str = "198.51.100.20",
) -> None:
    resolved = SimpleNamespace(ip=address, user="root", conn=connection, local_mode=local_mode)
    facts = MagicMock()
    facts.os_release.return_value = SimpleNamespace(pretty_name=os_info, id="ubuntu" if os_info else "")
    facts.free_disk_mb.return_value = disk_mb
    with (
        patch("meridian.commands.check.resolve_server", return_value=resolved),
        patch("meridian.commands.check.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.check.ServerFacts", return_value=facts),
        patch("meridian.commands.check.tcp_connect", return_value=external_reachable),
    ):
        run(ip=resolved.ip, sni="cdn.test")


def test_preflight_resolves_registered_hostname_for_external_probe() -> None:
    connection = FakeConnection(sni_reachable=True)
    with patch("meridian.commands.check.resolve_hostname", return_value="198.51.100.20") as resolve:
        _run_preflight(connection, address="exit.example.test")

    resolve.assert_called_once_with("exit.example.test", timeout=5)


def test_preflight_returns_findings_exit_when_required_sni_is_unreachable() -> None:
    connection = FakeConnection(sni_reachable=False)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, external_reachable=True)

    assert exc_info.value.exit_code == 4


def test_preflight_uses_server_resolver_without_third_party_metadata_calls() -> None:
    connection = FakeConnection(sni_reachable=True)

    _run_preflight(connection)

    commands = "\n".join(connection.commands)
    assert "getent ahostsv4" in commands
    assert "curl " not in commands
    assert "dig " not in commands


def test_preflight_returns_unavailable_exit_when_listener_evidence_fails() -> None:
    connection = FakeConnection(sni_reachable=True, ss_returncode=127)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 3
    assert sum(command.startswith("ss -H -tlnp") for command in connection.commands) == 1


@pytest.mark.parametrize(
    ("connection", "os_info", "disk_mb"),
    [
        (FakeConnection(sni_reachable=True), "", 4096),
        (FakeConnection(sni_reachable=True), "Ubuntu 24.04 LTS", None),
        (FakeConnection(sni_reachable=True, clock_available=False), "Ubuntu 24.04 LTS", 4096),
    ],
)
def test_preflight_fails_closed_when_required_server_facts_are_missing(
    connection: FakeConnection,
    os_info: str,
    disk_mb: int | None,
) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, os_info=os_info, disk_mb=disk_mb)

    assert exc_info.value.exit_code == 3


def test_preflight_findings_take_precedence_over_unavailable_evidence() -> None:
    connection = FakeConnection(sni_reachable=False, ss_returncode=127)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 4


def test_preflight_does_not_trust_process_name_without_meridian_marker() -> None:
    connection = FakeConnection(
        sni_reachable=True,
        ss_stdout='LISTEN 0 4096 *:443 *:* users:(("nginx",pid=42,fd=6))',
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, external_reachable=True)

    assert exc_info.value.exit_code == 4


def test_preflight_accepts_known_listener_with_meridian_marker() -> None:
    connection = FakeConnection(
        sni_reachable=True,
        ss_stdout='LISTEN 0 4096 *:443 *:* users:(("nginx",pid=42,fd=6))',
        meridian_managed=True,
    )

    _run_preflight(connection, external_reachable=True)


def test_preflight_reports_blocked_firewall_with_temporary_listener() -> None:
    connection = FakeConnection(sni_reachable=True)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, external_reachable=False)

    assert exc_info.value.exit_code == 4
    assert any("nohup python3 -c" in command for command in connection.commands)
    assert any(command.startswith("kill 4242") for command in connection.commands)


def test_preflight_marks_external_reachability_unavailable_in_local_mode() -> None:
    connection = FakeConnection(sni_reachable=True)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, local_mode=True)

    assert exc_info.value.exit_code == 3
    assert not any("nohup python3 -c" in command for command in connection.commands)


def test_preflight_treats_dns_negative_as_completed_finding() -> None:
    connection = FakeConnection(sni_reachable=True, dns_returncode=2)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 4


def test_preflight_treats_missing_dns_tool_as_unavailable() -> None:
    connection = FakeConnection(sni_reachable=True, dns_returncode=127)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 3


@pytest.mark.parametrize("returncode", [124, 255])
def test_preflight_treats_lost_sni_transport_as_unavailable(returncode: int) -> None:
    connection = FakeConnection(sni_reachable=False, sni_returncode=returncode)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 3


@pytest.mark.parametrize("returncode", [124, 255])
def test_preflight_treats_lost_dns_transport_as_unavailable(returncode: int) -> None:
    connection = FakeConnection(sni_reachable=True, dns_returncode=returncode)

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection)

    assert exc_info.value.exit_code == 3


def test_preflight_treats_lost_listener_ownership_probe_as_unavailable() -> None:
    connection = FakeConnection(
        sni_reachable=True,
        ss_stdout='LISTEN 0 4096 *:443 *:* users:(("nginx",pid=42,fd=6))',
        meridian_managed=True,
        ownership_returncode=255,
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_preflight(connection, external_reachable=True)

    assert exc_info.value.exit_code == 3
