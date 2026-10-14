"""`meridian panel`: admin access without hiding the panel, password only on request."""

from __future__ import annotations

import re

from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig

runner = CliRunner()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _save_panel(tmp_home) -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.10/0123abcd",
            admin_user="meridian-ab12",
            admin_pass="Mxsecretvalue9A",
        )
    )
    cluster.save()


def test_panel_prints_url_and_user_but_hides_password_by_default(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    _save_panel(tmp_home)

    result = runner.invoke(app, ["panel"])

    output = _plain(result.output)
    assert result.exit_code == 0
    assert "https://198.51.100.10/0123abcd/" in output
    assert "meridian-ab12" in output
    assert "Mxsecretvalue9A" not in output
    assert "--show-password" in output
    assert "Yours to change" in output


def test_panel_show_password_prints_it(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    _save_panel(tmp_home)

    result = runner.invoke(app, ["panel", "--show-password"])

    assert result.exit_code == 0
    assert "Mxsecretvalue9A" in _plain(result.output)


def test_panel_without_a_deployment_is_a_user_error(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    result = runner.invoke(app, ["panel"])

    assert result.exit_code == 2
    assert "No panel is deployed yet" in _plain(result.output)
