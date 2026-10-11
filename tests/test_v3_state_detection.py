"""Meridian 3.x users who upgrade get told what happened, not 'no cluster'."""

from __future__ import annotations

from pathlib import Path

import click
from typer.testing import CliRunner

from meridian.cli import app

runner = CliRunner()


def test_client_list_explains_meridian_3_state(tmp_home: Path) -> None:
    (tmp_home / "credentials" / "198.51.100.7").mkdir(parents=True)

    result = runner.invoke(app, ["client", "list"])
    output = click.unstyle(result.output)

    assert result.exit_code != 0
    assert "Meridian 3.x" in output
    assert "meridian-vpn<4" in output
    assert "No cluster configured" not in output


def test_server_list_explains_meridian_3_registry(tmp_home: Path) -> None:
    (tmp_home / "servers").write_text("198.51.100.7 root\n")

    result = runner.invoke(app, ["server", "list"])

    assert result.exit_code != 0
    assert "Meridian 3.x" in click.unstyle(result.output)


def test_fresh_install_still_says_deploy_first(tmp_home: Path) -> None:
    result = runner.invoke(app, ["client", "list"])

    assert "No cluster configured" in click.unstyle(result.output)
