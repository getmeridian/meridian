"""Shared fixtures for meridian tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set MERIDIAN_HOME to a temporary directory."""
    home = tmp_path / ".meridian"
    home.mkdir()
    monkeypatch.setenv("MERIDIAN_HOME", str(home))

    # Re-import config to pick up the new env var
    import meridian.config as cfg

    monkeypatch.setattr(cfg, "MERIDIAN_HOME", home)
    monkeypatch.setattr(cfg, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(cfg, "SERVER_PROFILES_FILE", home / "servers.json")

    return home


@pytest.fixture
def servers_file(tmp_home: Path) -> Path:
    """Return the JSON server profile path in the temporary home."""
    return tmp_home / "servers.json"
