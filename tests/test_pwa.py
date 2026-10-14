"""Tests for the pwa module (file generation, asset loading, upload)."""

from __future__ import annotations

import json
import re
import zlib

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.diagnostics.blocking import STALL_CLEARANCE_BYTES
from meridian.models import ProtocolURL
from meridian.pwa import (
    PROBE_FILE,
    connection_page_deployed,
    generate_client_files,
    load_pwa_static_assets,
    mark_connection_page_deployed,
    upload_client_files,
    upload_pwa_assets,
)
from tests.support.mock_connection import MockConnection

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


class TestConnectionPageMarkers:
    def test_page_marker_survives_cluster_reload(self, tmp_path) -> None:
        cluster = ClusterConfig(panel=PanelConfig(sub_path="connections"))
        user_uuid = "550e8400-e29b-41d4-a716-446655440000"

        mark_connection_page_deployed(cluster, user_uuid)
        mark_connection_page_deployed(cluster, user_uuid)
        path = tmp_path / "cluster.yml"
        cluster.save(path)

        loaded = ClusterConfig.load(path)
        assert connection_page_deployed(loaded, user_uuid) is True
        assert len(loaded.connection_page_markers) == 1
        assert user_uuid not in path.read_text()
        assert "connection_page_markers" not in loaded._extra


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
        assert all(app["icon"].startswith("data:image/webp;base64,") for app in catalog["apps"])

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


class TestUploadClientFiles:
    @pytest.fixture()
    def sample_files(self) -> dict[str, str]:
        return {
            "index.html": "<html>test</html>",
            "config.json": '{"version": 1}',
            "manifest.webmanifest": '{"display": "standalone"}',
            "sub.txt": "dmxlc3M6Ly8=",
        }

    def test_returns_empty_on_success(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        result = upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        assert result == ""

    def test_returns_error_on_failure(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        conn.when("put_bytes", rc=1)
        result = upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        assert result  # non-empty error string

    def test_creates_directory_with_uuid(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        conn.assert_called_with_pattern("mkdir -p /var/www/private/550e8400")
        assert conn.run_kwargs[0]["sensitive"] is True

    def test_create_directory_error_redacts_uuid(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        conn.when("mkdir -p", stderr="permission denied", rc=1)
        result = upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        assert "550e8400" not in result

    def test_uploads_all_files(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        assert len(conn.writes) == 4

    def test_chown_www_data_on_files(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        assert any("chown www-data:www-data" in c for c in conn.calls)
        assert all(kwargs["owner"] == "www-data:www-data" for _path, _data, kwargs in conn.writes)

    def test_writes_expected_paths(self, sample_files: dict[str, str]) -> None:
        conn = MockConnection()
        upload_client_files(conn, "550e8400-e29b-41d4-a716-446655440000", sample_files)
        written_paths = {path for path, _data, _kwargs in conn.writes}
        for filename in sample_files:
            assert f"/var/www/private/550e8400-e29b-41d4-a716-446655440000/{filename}" in written_paths


class TestUploadPWAAssets:
    def test_returns_empty_on_success(self) -> None:
        conn = MockConnection()
        result = upload_pwa_assets(conn)
        assert result == ""

    def test_returns_error_on_failure(self) -> None:
        conn = MockConnection()
        conn.when("put_bytes", rc=1)
        result = upload_pwa_assets(conn)
        assert result  # non-empty error string

    def test_creates_pwa_directory(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        conn.assert_called_with_pattern("mkdir -p /var/www/private/pwa")

    def test_uploads_every_shared_asset(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        assert len(conn.writes) == len(load_pwa_static_assets())

    def test_uses_file_api_for_assets(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        assert all(kwargs["owner"] == "www-data:www-data" for _path, _data, kwargs in conn.writes)
        assert all(kwargs["mode"] == "644" for _path, _data, kwargs in conn.writes)

    def test_sw_cache_version_is_content_hash(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        sw_content = conn.write_map["/var/www/private/pwa/sw.js"].decode()
        # Should NOT contain the static 'pwa-v1' version
        assert "'pwa-v1'" not in sw_content
        # Should contain a dynamic hash version like 'pwa-abcd1234'
        assert "pwa-" in sw_content

    def test_deploys_to_correct_paths(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        written_paths = {path for path, _data, _kwargs in conn.writes}
        for fname in ("app.js", "selfcheck.js", "styles.css", "sw.js", "icon.svg", "apps.json", "probe.bin"):
            assert f"/var/www/private/pwa/{fname}" in written_paths

    def test_selfcheck_reports_the_deployed_page_version(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        sw = conn.write_map["/var/www/private/pwa/sw.js"].decode()
        selfcheck = conn.write_map["/var/www/private/pwa/selfcheck.js"].decode()
        version = re.search(r"CACHE_VERSION = '(pwa-[0-9a-f]{8})'", sw)
        assert version is not None
        assert f"var PAGE_VERSION = '{version.group(1)}';" in selfcheck

    def test_cache_version_follows_the_app_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A catalog-only change must still roll the service-worker cache."""
        conn = MockConnection()
        upload_pwa_assets(conn)
        before = conn.write_map["/var/www/private/pwa/sw.js"]
        monkeypatch.setattr("meridian.pwa.render_apps_catalog", lambda: '{"version": 1, "apps": []}')
        conn = MockConnection()
        upload_pwa_assets(conn)
        assert conn.write_map["/var/www/private/pwa/sw.js"] != before
