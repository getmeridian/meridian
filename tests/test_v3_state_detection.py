"""Meridian 3.x users who upgrade get told what happened, not 'no cluster'."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from meridian.cli import app
from tests.support.text import unstyle

runner = CliRunner()


def test_client_list_explains_meridian_3_state(tmp_home: Path) -> None:
    (tmp_home / "credentials" / "198.51.100.7").mkdir(parents=True)

    result = runner.invoke(app, ["client", "list"])
    output = unstyle(result.output)

    assert result.exit_code != 0
    assert "Meridian 3.x" in output
    assert "meridian-vpn<4" in output
    assert "No cluster configured" not in output


def test_server_list_explains_meridian_3_registry(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr("meridian.commands.server.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    (tmp_home / "servers").write_text("198.51.100.7 root\n")

    result = runner.invoke(app, ["server", "list"])

    assert result.exit_code != 0
    assert "Meridian 3.x" in unstyle(result.output)


def test_fresh_install_still_says_deploy_first(tmp_home: Path) -> None:
    result = runner.invoke(app, ["client", "list"])

    assert "No fleet yet" in unstyle(result.output)
    assert "meridian deploy" in unstyle(result.output)


def test_v4_home_defaults_to_xdg_and_adopts_an_existing_beta_fleet(tmp_path, monkeypatch) -> None:
    """Beta testers keep their fleet when they move from the meridian4 alias to `meridian`."""
    import meridian.config as cfg

    monkeypatch.setattr(cfg.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(cfg, "LEGACY_HOME", tmp_path / ".meridian")
    assert cfg.default_home() == tmp_path / ".config" / "meridian"

    (tmp_path / ".meridian").mkdir()
    (tmp_path / ".meridian" / "servers").write_text("")  # 3.x state never becomes a 4.x home
    assert cfg.default_home() == tmp_path / ".config" / "meridian"

    beta = tmp_path / ".meridian-v4"
    beta.mkdir()
    (beta / "servers.json").write_text("{}")
    assert cfg.default_home() == beta

    (tmp_path / ".config" / "meridian").mkdir(parents=True)
    assert cfg.default_home() == tmp_path / ".config" / "meridian"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "xdg" / "meridian").mkdir(parents=True)
    assert cfg.default_home() == tmp_path / "xdg" / "meridian"
