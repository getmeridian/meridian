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


CONTEXT_BOUND = (
    "CONTEXT",
    "FLEET_HOME",
    "HOME_DISPLAY",
    "CLUSTER_CONFIG",
    "CLUSTER_BACKUP",
    "SERVER_PROFILES_FILE",
    "SETUP_DRAFT_FILE",
)


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set MERIDIAN_HOME to a temporary directory."""
    home = tmp_path / ".meridian"
    home.mkdir()
    monkeypatch.setenv("MERIDIAN_HOME", str(home))

    # Re-import config to pick up the new env var
    import meridian.config as cfg

    monkeypatch.setattr(cfg, "PINNED_HOME", True)
    monkeypatch.setattr(cfg, "INSTALL_HOME", home)
    monkeypatch.setattr(cfg, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(cfg, "PREFERENCES_FILE", home / "preferences.json")
    monkeypatch.setattr(cfg, "MERIDIAN_SSH_KEY_FILE", home / "ssh" / "meridian_ed25519")
    # Per-fleet paths: setattr each so monkeypatch restores them, then let bind_context fill them in.
    for name in CONTEXT_BOUND:
        monkeypatch.setattr(cfg, name, getattr(cfg, name))
    cfg.select_context()

    return home


@pytest.fixture
def servers_file(tmp_home: Path) -> Path:
    """Return the JSON server profile path in the temporary home."""
    return tmp_home / "servers.json"


@pytest.fixture(autouse=True)
def _isolated_legacy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's real 3.x ~/.meridian decide what a test sees."""
    import meridian.config as cfg

    monkeypatch.setattr(cfg, "LEGACY_HOME", tmp_path / "legacy-home")


@pytest.fixture(autouse=True)
def _instant_stall_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stalled-connection retries keep their count but never pause the suite."""
    monkeypatch.setattr("meridian.reconciler.stall_retry.STALL_DELAYS", (0.0, 0.0, 0.0, 0.0))


@pytest.fixture(autouse=True)
def _reachable_retiring_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Servers a change removes answer by default; tests of the forget offer stub them unreachable."""
    monkeypatch.setattr("meridian.commands._helpers.ssh_port_answers", lambda _host, _port: True)


@pytest.fixture(autouse=True)
def _offline_registry_country(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit-country lookups never leave the test process; tests that need one inject a transport."""
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"tests are offline: {request.url}")

    monkeypatch.setattr("meridian.registry_country.DEFAULT_TRANSPORT", httpx.MockTransport(refuse))


@pytest.fixture(scope="session")
def xray_binary() -> Path:
    """The pinned Xray from `MERIDIAN_TEST_XRAY` (CI supplies it), its geodata files beside it."""
    import subprocess

    from meridian.config import XRAY_VERSION

    configured = os.environ.get("MERIDIAN_TEST_XRAY")
    if not configured:
        pytest.skip("set MERIDIAN_TEST_XRAY to run real Xray tests")
    binary = Path(configured).resolve()
    version = subprocess.run([str(binary), "version"], capture_output=True, text=True, check=True, timeout=10)
    assert version.stdout.startswith(f"Xray {XRAY_VERSION} "), version.stdout
    return binary
