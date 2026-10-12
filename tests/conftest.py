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
    monkeypatch.setattr(cfg, "CREDS_BASE", home / "credentials")
    monkeypatch.setattr(cfg, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(cfg, "SERVERS_FILE", home / "servers")

    return home


@pytest.fixture
def servers_file(tmp_home: Path) -> Path:
    """Return path to the servers file in tmp home."""
    return tmp_home / "servers"


@pytest.fixture
def creds_dir(tmp_home: Path) -> Path:
    """Create and return a credentials directory for a test server."""
    d = tmp_home / "credentials" / "1.2.3.4"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_proxy_yml(creds_dir: Path) -> Path:
    """Write a sample v2 proxy.yml and return its path."""
    content = """\
version: 2
panel_configured: true
panel:
  username: admin
  password: "s3cret!pass"
  web_base_path: abc123
  info_page_path: info456
  port: 2053
server:
  ip: 1.2.3.4
  sni: www.cloudflare.com
  scanned_sni: dl.google.com
protocols:
  reality:
    uuid: 550e8400-e29b-41d4-a716-446655440000
    private_key: WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo
    public_key: K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy
    short_id: abcd1234
  wss:
    uuid: 660e8400-e29b-41d4-a716-446655440001
    ws_path: ws789
clients:
  - name: default
    added: "2026-01-01T00:00:00Z"
    reality_uuid: 550e8400-e29b-41d4-a716-446655440000
    wss_uuid: 660e8400-e29b-41d4-a716-446655440001
"""
    proxy = creds_dir / "proxy.yml"
    proxy.write_text(content)
    proxy.chmod(0o600)
    return proxy


@pytest.fixture
def sample_v1_proxy_yml(creds_dir: Path) -> Path:
    """Write a sample v1 (flat) proxy.yml and return its path."""
    content = """\
panel_username: admin
panel_password: "s3cret!pass"
panel_web_base_path: abc123
info_page_path: info456
ws_path: ws789
reality_uuid: 550e8400-e29b-41d4-a716-446655440000
reality_private_key: WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo
reality_public_key: K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy
reality_short_id: abcd1234
reality_sni: www.cloudflare.com
wss_uuid: 660e8400-e29b-41d4-a716-446655440001
xhttp_uuid: ""
xhttp_enabled: false
exit_ip: 1.2.3.4
domain: ""
scanned_sni: dl.google.com
"""
    proxy = creds_dir / "proxy.yml"
    proxy.write_text(content)
    proxy.chmod(0o600)
    return proxy
