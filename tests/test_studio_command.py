"""Tests for the executable Studio command boundary."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.studio import DEFAULT_PORT, _choose_port, run


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


@pytest.mark.parametrize(
    ("busy", "expected"),
    [
        (set(), DEFAULT_PORT),
        ({DEFAULT_PORT}, DEFAULT_PORT + 1),
        ({DEFAULT_PORT, DEFAULT_PORT + 1, DEFAULT_PORT + 2}, DEFAULT_PORT + 3),
    ],
    ids=["free", "next", "skips-several"],
)
def test_default_port_is_stable_so_browser_drafts_survive_restarts(busy: set[int], expected: int) -> None:
    with patch("meridian.commands.studio._port_is_free", side_effect=lambda _host, port: port not in busy):
        assert _choose_port("127.0.0.1", None) == expected


def test_default_port_falls_back_to_any_free_port_when_its_range_is_busy() -> None:
    with patch("meridian.commands.studio._port_is_free", return_value=False):
        port = _choose_port("127.0.0.1", None)

    assert port not in range(DEFAULT_PORT, DEFAULT_PORT + 10)
    assert 0 < port < 65536


def test_port_zero_picks_any_free_port() -> None:
    with patch("meridian.commands.studio._port_is_free") as probe:
        port = _choose_port("127.0.0.1", 0)

    probe.assert_not_called()
    assert port > 0


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
