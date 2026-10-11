"""`meridian deploy` drives the same reviewed topology runtime as `meridian setup`."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.console import set_json_mode, set_quiet_mode
from meridian.handoff import HandoffLinks

runner = CliRunner()


def _invoke(args: list[str]):
    try:
        return runner.invoke(app, args)
    finally:
        set_json_mode(False)
        set_quiet_mode(False)


def test_dry_run_compiles_a_single_server_topology_without_touching_state(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")

    result = _invoke(["deploy", "198.51.100.10", "--domain", "vpn.example.com", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["mode"] == "first_deploy"
    assert data["ports"]["reality_port"] > 0 and data["ports"]["xhttp_port"] > 0
    assert data["xhttp_path"] and data["ws_path"]
    assert not (tmp_home / "cluster.yml").exists()
    assert not (tmp_home / "servers.json").exists()


def test_deploy_applies_the_intent_and_hands_off_a_page(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    conn = MagicMock(port=22)
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", conn=conn)
    runtime = MagicMock()
    runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, failed=[], plan_hash="h" * 64)
    runtime.publish_handoff.return_value = [
        HandoffLinks(
            "alice",
            subscription_url="https://198.51.100.10/pages/sub/s1",
            page_url="https://198.51.100.10/pages/u1/",
        )
    ]

    with (
        patch("meridian.commands.deploy.resolve_server", return_value=resolved),
        patch("meridian.commands.deploy.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.deploy.check_ports") as check_ports,
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
    ):
        result = _invoke(["deploy", "198.51.100.10", "--client-name", "alice", "--no-geo-block", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    check_ports.assert_called_once()
    intent = runtime.apply_intent.call_args.args[0]
    assert intent.access.users == ["alice"]
    assert intent.exits[0].geo_block is False and intent.harden is True
    runtime.verify.assert_called_once()
    runtime.publish_handoff.assert_called_once_with(["alice"])
    data = json.loads(result.stdout)["data"]
    assert data["mode"] == "first_deploy"
    assert data["sni"] == "www.microsoft.com"
    # Machine output redacts capability URLs by policy; humans get them on stderr.
    assert data["connection_page_url"].startswith("https://198.51.100.10/")


def test_hysteria2_without_a_domain_is_refused_before_ssh(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    with patch("meridian.commands.deploy.resolve_server") as resolve_server:
        result = _invoke(["deploy", "198.51.100.10", "--hysteria2", "--dry-run"])

    assert result.exit_code == 2
    assert "Hysteria2 needs a domain" in click.unstyle(result.output)
    resolve_server.assert_not_called()
