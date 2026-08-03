"""Tests for diagnostics module — redaction, formatting, cert parsing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.diagnostics import (
    _check_cert_expiry,
    _EvidenceConnection,
    _format_sections,
    _redact_secrets,
    run,
)
from meridian.ssh import CommandResult, SSHError


class TestRedactSecrets:
    """Verify secret redaction patterns."""

    def test_redacts_uuid(self) -> None:
        text = "client id: 550e8400-e29b-41d4-a716-446655440000"
        assert "[UUID-REDACTED]" in _redact_secrets(text)
        assert "550e8400" not in _redact_secrets(text)

    def test_redacts_password(self) -> None:
        text = "Password: s3cret!pass"
        result = _redact_secrets(text)
        assert "s3cret" not in result
        assert "Password: [REDACTED]" in result

    def test_redacts_key(self) -> None:
        text = "Key=WBNp7SHzGMaqp6ohXMfJHUy"
        result = _redact_secrets(text)
        assert "WBNp7" not in result
        assert "Key=[REDACTED]" in result

    def test_preserves_non_secret_text(self) -> None:
        text = "nginx started on port 443"
        assert _redact_secrets(text) == text

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            (
                "Authorization: Bearer eyJabcdefgh.ijklmnop.qrstuvwx",
                "eyJabcdefgh.ijklmnop.qrstuvwx",
            ),
            ("panel=https://panel.test/unguessable/path?token=value", "unguessable"),
            ("DATABASE_URL=postgresql://admin:pass@db.test/meridian", "admin:pass"),
            ('{"api_token":"top-secret-token"}', "top-secret-token"),
            (
                "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----",
                "private-material",
            ),
        ],
    )
    def test_redacts_structured_and_free_form_secret_shapes(self, text: str, secret: str) -> None:
        result = _redact_secrets(text)

        assert secret not in result
        assert "[REDACTED]" in result


class TestFormatSections:
    """Verify markdown formatting."""

    def test_formats_as_markdown(self) -> None:
        sections = [("Title", "body text")]
        result = _format_sections(sections)
        assert "### Title" in result
        assert "```\nbody text\n```" in result

    def test_multiple_sections(self) -> None:
        sections = [("A", "1"), ("B", "2")]
        result = _format_sections(sections)
        assert "### A" in result
        assert "### B" in result


class TestCheckCertExpiry:
    """Verify TLS certificate expiry parsing."""

    def test_valid_cert(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 10) -> object:
                from types import SimpleNamespace

                return SimpleNamespace(stdout="notAfter=Dec 31 23:59:59 2030 GMT", returncode=0)

        result = _check_cert_expiry(FakeConn())
        assert "valid until 2030-12-31" in result
        assert "days" in result

    def test_expired_cert(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 10) -> object:
                from types import SimpleNamespace

                return SimpleNamespace(stdout="notAfter=Jan 01 00:00:00 2020 GMT", returncode=0)

        result = _check_cert_expiry(FakeConn())
        assert "EXPIRED" in result

    def test_no_cert(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 10) -> object:
                from types import SimpleNamespace

                return SimpleNamespace(stdout="", returncode=1)

        result = _check_cert_expiry(FakeConn())
        assert "could not check" in result


def test_doctor_uses_v4_roles_and_redacts_the_final_report() -> None:
    class FakeConn:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run(self, command: str, **_kwargs: object) -> CommandResult:
            self.commands.append(command)
            return CommandResult(
                args=command,
                returncode=0,
                stdout="api_token=diagnostic-secret\n",
            )

    connection = FakeConn()
    resolved = SimpleNamespace(ip="198.51.100.20", user="root", conn=connection)
    intent = SimpleNamespace(
        control=SimpleNamespace(server_ref="control-a"),
        exits=[],
        transparent_relays=[],
        routing_gateways=[],
    )
    cluster = MagicMock()
    cluster.find_node.return_value = None
    cluster.panel = SimpleNamespace(server_ip="198.51.100.20", deployed_with="4.0.0")
    cluster.relays = []
    cluster.topology_intent = intent

    topology = MagicMock()
    topology.panel.server_ip = "198.51.100.20"
    topology.find_node.return_value = None
    topology.relays = []

    registry = MagicMock()
    registry.find.return_value = SimpleNamespace(id="control-a")
    deployment = SimpleNamespace(
        kind="v4",
        public_tcp_ports=(7443,),
        public_udp_ports=(9443,),
        internal_ports={"control/https": 18443, "exit-a/reality": 39001},
        https_routes=(
            SimpleNamespace(
                kind="managed",
                port=7443,
                tls_sni="panel.example.com",
                host_header="panel.example.com",
            ),
        ),
    )

    facts = MagicMock()
    facts.os_release.return_value = SimpleNamespace(pretty_name="Ubuntu 24.04 LTS", id="ubuntu")
    facts.docker_state.return_value = SimpleNamespace(installed=True, version="27", compose_available=True)
    facts.ufw_state.return_value = SimpleNamespace(active=True, installed=True)
    facts.arch.return_value = "x86_64"
    facts.dpkg_arch.return_value = "amd64"
    facts.ssh_ports.return_value = [22]
    facts.free_disk_mb.return_value = 4096

    with (
        patch("meridian.commands.diagnostics.ServerRegistry", return_value=registry),
        patch("meridian.commands.diagnostics.resolve_server", return_value=resolved),
        patch("meridian.commands.diagnostics.ensure_server_connection", return_value=resolved),
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.diagnostics.topology_from_local_cluster", return_value=topology),
        patch("meridian.commands.diagnostics.deployment_verification_context", return_value=deployment),
        patch("meridian.commands.diagnostics.ServerFacts", return_value=facts),
        patch("meridian.commands.diagnostics.err_console.print") as render,
    ):
        run(ip="198.51.100.20")

    rendered = "\n".join(str(call.args[0]) for call in render.call_args_list if call.args)
    assert "Roles: panel" in rendered
    assert "not expected for this server's configured roles" in rendered
    assert "diagnostic-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert not any("pgrep -f xray" in command for command in connection.commands)
    assert not any("docker logs remnawave-node" in command for command in connection.commands)
    listener_command = next(command for command in connection.commands if command.startswith("ss -tulnp"))
    assert all(f"sport = :{port}" in listener_command for port in (7443, 9443, 18443, 39001))
    assert "sport = :8444" not in listener_command
    assert any(
        "127.0.0.1:7443" in command and "-servername panel.example.com" in command for command in connection.commands
    )


def test_doctor_rejects_invalid_sni_before_resolving_server() -> None:
    with (
        patch("meridian.commands.diagnostics.resolve_server") as resolve,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(sni="bad host; command")

    assert exc_info.value.exit_code == 2
    resolve.assert_not_called()


def test_evidence_connection_converts_lost_ssh_to_unavailable_result() -> None:
    connection = MagicMock()
    connection.run.side_effect = SSHError("connection dropped")

    wrapped = _EvidenceConnection(connection)
    result = wrapped.run("uname -a")

    assert result.returncode == 255
    assert wrapped.unavailable is True


def test_doctor_returns_partial_report_and_exit_three_when_tool_is_missing() -> None:
    connection = MagicMock()
    connection.run.return_value = CommandResult(args="diagnostic", returncode=127, stderr="missing")
    resolved = SimpleNamespace(ip="198.51.100.30", user="root", conn=connection)
    cluster = MagicMock()
    cluster.find_node.return_value = None
    cluster.panel = SimpleNamespace(server_ip="", deployed_with="")
    cluster.relays = []
    cluster.topology_intent = None
    topology = MagicMock()
    topology.panel.server_ip = ""
    topology.find_node.return_value = None
    topology.relays = []
    facts = MagicMock()
    facts.os_release.return_value = SimpleNamespace(pretty_name="", id="unknown")
    facts.docker_state.return_value = SimpleNamespace(installed=False, version="", compose_available=False)
    facts.ufw_state.return_value = SimpleNamespace(active=False, installed=False)
    facts.arch.return_value = ""
    facts.dpkg_arch.return_value = ""
    facts.ssh_ports.return_value = []
    facts.free_disk_mb.return_value = 0

    with (
        patch("meridian.commands.diagnostics.ServerRegistry") as registry,
        patch("meridian.commands.diagnostics.resolve_server", return_value=resolved),
        patch("meridian.commands.diagnostics.ensure_server_connection", return_value=resolved),
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.diagnostics.topology_from_local_cluster", return_value=topology),
        patch("meridian.commands.diagnostics.ServerFacts", return_value=facts),
        patch("meridian.commands.diagnostics.err_console.print") as render,
        pytest.raises(typer.Exit) as exc_info,
    ):
        registry.return_value.find.return_value = None
        run(ip="198.51.100.30")

    assert exc_info.value.exit_code == 3
    assert any("Diagnostics collected" in str(call.args[0]) for call in render.call_args_list if call.args)


def test_doctor_keeps_relay_only_diagnostics_role_specific() -> None:
    connection = MagicMock()
    connection.run.return_value = CommandResult(args="diagnostic", returncode=0, stdout="ok\n")
    resolved = SimpleNamespace(ip="198.51.100.30", user="root", conn=connection)
    cluster = MagicMock()
    cluster.find_node.return_value = None
    cluster.panel = SimpleNamespace(server_ip="198.51.100.20", deployed_with="4.0.0")
    cluster.relays = []
    cluster.topology_intent = None

    relay = SimpleNamespace(ip="198.51.100.30", sni="", port=7443)
    topology = MagicMock()
    topology.panel.server_ip = "198.51.100.20"
    topology.find_node.return_value = None
    topology.relays = [relay]

    facts = MagicMock()
    facts.os_release.return_value = SimpleNamespace(pretty_name="Ubuntu 24.04 LTS", id="ubuntu")
    facts.docker_state.return_value = SimpleNamespace(installed=False, version="", compose_available=False)
    facts.ufw_state.return_value = SimpleNamespace(active=True, installed=True)
    facts.arch.return_value = "x86_64"
    facts.dpkg_arch.return_value = "amd64"
    facts.ssh_ports.return_value = [22]
    facts.free_disk_mb.return_value = 4096

    with (
        patch("meridian.commands.diagnostics.ServerRegistry"),
        patch("meridian.commands.diagnostics.resolve_server", return_value=resolved),
        patch("meridian.commands.diagnostics.ensure_server_connection", return_value=resolved),
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.diagnostics.topology_from_local_cluster", return_value=topology),
        patch("meridian.commands.diagnostics.ServerFacts", return_value=facts),
        patch("meridian.commands.diagnostics.err_console.print") as render,
    ):
        run(ip="198.51.100.30")

    rendered = "\n".join(str(call.args[0]) for call in render.call_args_list if call.args)
    commands = [call.args[0] for call in connection.run.call_args_list]
    assert "Roles: relay" in rendered
    assert "Remnawave Node Logs" not in rendered
    assert "Nginx Errors" not in rendered
    assert "TLS Certificate" not in rendered
    assert "Geo-blocking" not in rendered
    assert any("ss -tlnp sport = :7443" in command for command in commands)
    assert not any("pgrep -f xray" in command for command in commands)
