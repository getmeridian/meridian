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
