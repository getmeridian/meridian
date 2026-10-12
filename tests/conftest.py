"""Shared fixtures for meridian tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# meridian.config resolves its paths at import time, so isolate the suite from
# the developer's real ~/.meridian (cluster.yml, SSH keys) before anything
# imports it, and keep tests off the network-bound PyPI update check.
os.environ["MERIDIAN_HOME"] = tempfile.mkdtemp(prefix="meridian-test-home-")
os.environ["MERIDIAN_DISABLE_UPDATE_CHECK"] = "1"


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set MERIDIAN_HOME to a temporary directory."""
    home = tmp_path / ".meridian"
    home.mkdir()
    monkeypatch.setenv("MERIDIAN_HOME", str(home))

    # Re-import config to pick up the new env var
    import meridian.config as cfg

    monkeypatch.setattr(cfg, "MERIDIAN_HOME", home)
    monkeypatch.setattr(cfg, "CLUSTER_CONFIG", home / "cluster.yml")
    monkeypatch.setattr(cfg, "CLUSTER_BACKUP", home / "cluster.yml.bak")
    monkeypatch.setattr(cfg, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(cfg, "SERVER_PROFILES_FILE", home / "servers.json")
    monkeypatch.setattr(cfg, "SETUP_DRAFT_FILE", home / "setup.json")
    monkeypatch.setattr(cfg, "PREFERENCES_FILE", home / "preferences.json")
    monkeypatch.setattr(cfg, "MERIDIAN_SSH_KEY_FILE", home / "ssh" / "meridian_ed25519")

    return home


@pytest.fixture
def servers_file(tmp_home: Path) -> Path:
    """Return the JSON server profile path in the temporary home."""
    return tmp_home / "servers.json"
