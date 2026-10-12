"""Smoke tests for CLI commands using CliRunner."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from meridian.cli import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestCLIBasics:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "meridian" in _strip_ansi(result.output)

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "deploy" in output
        assert "client" in output
        assert "server" in output

    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app)
        output = _strip_ansi(result.output)
        assert "deploy" in output or "Usage" in output


class TestSubcommandHelp:
    def test_deploy_help(self) -> None:
        result = runner.invoke(app, ["deploy", "--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "--domain" in output
        assert "--sni" in output

    def test_client_help(self) -> None:
        result = runner.invoke(app, ["client", "--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "add" in output
        assert "list" in output
        assert "remove" in output

    def test_server_help(self) -> None:
        result = runner.invoke(app, ["server", "--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "add" in output
        assert "list" in output
        assert "remove" in output

    def test_preflight_help(self) -> None:
        result = runner.invoke(app, ["preflight", "--help"])
        assert result.exit_code == 0
        assert "--ai" in _strip_ansi(result.output)
        assert "--ssh-port" in _strip_ansi(result.output)

    def test_doctor_help(self) -> None:
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--ai" in _strip_ansi(result.output)

    def test_rage_alias(self) -> None:
        result = runner.invoke(app, ["rage", "--help"])
        assert result.exit_code == 0
        assert "--ai" in _strip_ansi(result.output)

    def test_update_help(self) -> None:
        result = runner.invoke(app, ["update", "--help"])
        assert result.exit_code == 0
