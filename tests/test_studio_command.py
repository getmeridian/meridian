"""Tests for the executable Studio command boundary."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.studio import _choose_port, run


def test_explicit_assets_directory_must_contain_studio(tmp_path: Path) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        run(assets_dir=str(tmp_path), no_open=True)

    assert exc_info.value.exit_code == 2


def test_missing_packaged_assets_is_a_system_failure() -> None:
    with (
        patch("meridian.commands.studio.resolve_studio_assets", return_value=None),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(no_open=True)

    assert exc_info.value.exit_code == 3


def test_requested_port_must_be_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])

        with pytest.raises(typer.Exit) as exc_info:
            _choose_port("127.0.0.1", port)

    assert exc_info.value.exit_code == 2


def test_missing_studio_extra_explains_how_to_install(monkeypatch) -> None:
    import sys

    from typer.testing import CliRunner

    import meridian.cli as cli
    from meridian.cli import app

    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.delitem(sys.modules, "meridian.commands.studio", raising=False)
    monkeypatch.setitem(sys.modules, "uvicorn", None)

    result = CliRunner().invoke(app, ["studio", "--no-open"])

    assert result.exit_code == 2
    assert "meridian-vpn[studio]" in result.output


def test_studio_hands_the_browser_its_key_only_in_the_url_fragment(tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_app(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    with (
        patch("meridian.commands.studio.resolve_studio_assets", return_value=tmp_path),
        patch("meridian.commands.studio.create_engine_app", side_effect=fake_app),
        patch("meridian.commands.studio.uvicorn.run") as serve,
        patch("meridian.commands.studio.webbrowser.open") as browser,
    ):
        run(port=0, no_open=True)

    key = str(captured["session_key"])
    port = int(str(captured["port"]))
    assert len(key) >= 32
    assert f"http://127.0.0.1:{port}/studio/#k={key}" in capsys.readouterr().err.replace("\n", "")
    serve.assert_called_once()
    browser.assert_not_called()


def test_each_launch_gets_a_new_key(tmp_path: Path) -> None:
    keys: list[str] = []

    def fake_app(**kwargs: object) -> object:
        keys.append(str(kwargs["session_key"]))
        return object()

    with (
        patch("meridian.commands.studio.resolve_studio_assets", return_value=tmp_path),
        patch("meridian.commands.studio.create_engine_app", side_effect=fake_app),
        patch("meridian.commands.studio.uvicorn.run"),
    ):
        run(port=0, no_open=True)
        run(port=0, no_open=True)

    assert keys[0] != keys[1]
