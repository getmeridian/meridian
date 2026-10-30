"""Shared connection-page rendering and package asset versions."""

from __future__ import annotations

import json
import re
import zlib

import pytest

from meridian.diagnostics.blocking import STALL_CLEARANCE_BYTES
from meridian.models import ProtocolURL
from meridian.pwa import (
    PROBE_FILE,
    generate_client_files,
    load_pwa_assets,
    load_pwa_static_assets,
)

REALITY_URL = "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=reality#Test"


@pytest.fixture()
def protocol_urls() -> list[ProtocolURL]:
    return [
        ProtocolURL(key="reality", label="Primary", url=REALITY_URL, qr_b64="dGVzdA=="),
    ]


class TestGenerateClientFiles:
    def test_returns_shell_files_without_shadow_subscription(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1")
        assert "index.html" in files
        assert "config.json" in files
        assert "manifest.webmanifest" in files
        assert "sub.txt" not in files

    def test_config_json_has_client_name(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1", client_name="alice")
        config = json.loads(files["config.json"])
        assert config["client_name"] == "alice"

    def test_index_html_is_shell(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1")
        assert "app.js" in files["index.html"]
        assert "vless://" not in files["index.html"]

    def test_manifest_is_valid_json(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1")
        parsed = json.loads(files["manifest.webmanifest"])
        assert parsed["display"] == "standalone"

    def test_config_uses_canonical_remnawave_subscription(self, protocol_urls: list[ProtocolURL]) -> None:
        canonical_url = "https://vpn.example.com/api/sub/abc123"

        files = generate_client_files(
            protocol_urls,
            "198.51.100.1",
            subscription_url=canonical_url,
        )

        config = json.loads(files["config.json"])
        assert config["subscription_url"] == canonical_url


class TestLoadPWAStaticAssets:
    def test_loads_all_assets(self) -> None:
        assets = load_pwa_static_assets()
        assert "app.js" in assets
        assert "styles.css" in assets
        assert "sw.js" in assets
        assert "icon.svg" in assets
        assert "apps.json" in assets

    def test_shared_catalog_carries_icons(self) -> None:
        catalog = json.loads(load_pwa_static_assets()["apps.json"])
        assert catalog["apps"][0]["name"] == "Happ"
        assert all(
            app["icon"].startswith(("data:image/webp;base64,", "data:image/svg+xml;base64,")) for app in catalog["apps"]
        )

    def test_probe_asset_is_large_and_incompressible(self) -> None:
        """The self-check needs a >=64 KB asset that clears the throttling freeze point."""
        probe = load_pwa_static_assets()[PROBE_FILE]
        assert len(probe) >= 64 * 1024
        assert len(probe) > 2 * STALL_CLEARANCE_BYTES
        assert len(zlib.compress(probe)) > 0.99 * len(probe)

    def test_probe_asset_is_deterministic(self) -> None:
        assert load_pwa_static_assets()[PROBE_FILE] == load_pwa_static_assets()[PROBE_FILE]

    def test_app_js_stays_a_valid_cli_stall_target(self) -> None:
        """`meridian test --probe` streams pwa/app.js uncompressed and needs it past the freeze point."""
        assert len(load_pwa_static_assets()["app.js"]) > STALL_CLEARANCE_BYTES

    def test_assets_are_bytes(self) -> None:
        assets = load_pwa_static_assets()
        for name, content in assets.items():
            assert isinstance(content, bytes), f"{name} should be bytes"

    def test_icon_is_valid_svg(self) -> None:
        assets = load_pwa_static_assets()
        svg = assets["icon.svg"].decode("utf-8")
        assert "<svg" in svg

    def test_app_js_not_empty(self) -> None:
        assets = load_pwa_static_assets()
        assert len(assets["app.js"]) > 100

    def test_sw_js_has_cache_version(self) -> None:
        assets = load_pwa_static_assets()
        sw = assets["sw.js"].decode("utf-8")
        assert "CACHE_VERSION" in sw


class TestPwaAssetVersions:
    def test_worker_and_self_check_use_the_same_content_version(self) -> None:
        assets = load_pwa_assets()
        assert re.fullmatch(r"pwa-[0-9a-f]{16}", assets.version)
        assert f"CACHE_VERSION = '{assets.version}'" in assets.files["sw.js"].decode()
        assert f"PAGE_VERSION = '{assets.version}'" in assets.files["selfcheck.js"].decode()
        assert "'pwa-v1'" not in assets.files["sw.js"].decode()

    @pytest.mark.parametrize("name", ["sw.js", "icon.svg", "support.html", "apps.json"])
    def test_every_changed_shared_asset_refreshes_the_version(self, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        before = load_pwa_assets()
        raw = load_pwa_static_assets()
        raw[name] += b"\nchanged"
        monkeypatch.setattr("meridian.pwa.load_pwa_static_assets", lambda: raw)
        assert load_pwa_assets().version != before.version
