"""Tests for the pwa module (file generation, asset loading, upload)."""

from __future__ import annotations

import json

import pytest

from meridian.models import ProtocolURL, RelayURLSet
from meridian.pwa import generate_client_files, load_pwa_static_assets, upload_client_files, upload_pwa_assets
from tests.support.mock_connection import MockConnection

REALITY_URL = "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=reality#Test"


@pytest.fixture()
def protocol_urls() -> list[ProtocolURL]:
    return [
        ProtocolURL(key="reality", label="Primary", url=REALITY_URL, qr_b64="dGVzdA=="),
    ]


class TestGenerateClientFiles:
    def test_returns_four_files(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1")
        assert "index.html" in files
        assert "config.json" in files
        assert "manifest.webmanifest" in files
        assert "sub.txt" in files

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

    def test_sub_txt_not_empty(self, protocol_urls: list[ProtocolURL]) -> None:
        files = generate_client_files(protocol_urls, "198.51.100.1")
        assert len(files["sub.txt"]) > 0

    def test_relay_entries_in_config(self, protocol_urls: list[ProtocolURL]) -> None:
        relays = [
            RelayURLSet(
                relay_ip="203.0.113.10",
                relay_name="test-relay",
                urls=[ProtocolURL(key="reality", label="Via relay", url=REALITY_URL)],
            ),
        ]
        files = generate_client_files(protocol_urls, "198.51.100.1", relay_entries=relays)
        config = json.loads(files["config.json"])
        assert len(config["relays"]) == 1
        assert config["relays"][0]["name"] == "test-relay"


class TestLoadPWAStaticAssets:
    def test_loads_all_assets(self) -> None:
        assets = load_pwa_static_assets()
        assert "app.js" in assets
        assert "styles.css" in assets
        assert "sw.js" in assets
        assert "icon.svg" in assets

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


# ---------------------------------------------------------------------------
# TestUploadClientFiles
# ---------------------------------------------------------------------------


class TestUploadClientFiles:
    """Tests for upload_client_files() — per-client file deployment."""

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


# ---------------------------------------------------------------------------
# TestUploadPWAAssets
# ---------------------------------------------------------------------------


class TestUploadPWAAssets:
    """Tests for upload_pwa_assets() — shared static asset deployment."""

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

    def test_uploads_all_four_static_files(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        assert len(conn.writes) == 4

    def test_uses_file_api_for_assets(self) -> None:
        conn = MockConnection()
        upload_pwa_assets(conn)
        assert all(kwargs["owner"] == "www-data:www-data" for _path, _data, kwargs in conn.writes)
        assert all(kwargs["mode"] == "644" for _path, _data, kwargs in conn.writes)

    def test_sw_cache_version_is_content_hash(self) -> None:
        """SW cache version is replaced with a content-derived hash at upload time."""
        conn = MockConnection()
        upload_pwa_assets(conn)
        sw_content = conn.write_map["/var/www/private/pwa/sw.js"].decode()
        # Should NOT contain the static 'pwa-v1' version
        assert "'pwa-v1'" not in sw_content
        # Should contain a dynamic hash version like 'pwa-abcd1234'
        assert "pwa-" in sw_content

    def test_deploys_to_correct_paths(self) -> None:
        """Each static file should be written to /var/www/private/pwa/."""
        conn = MockConnection()
        upload_pwa_assets(conn)
        written_paths = {path for path, _data, _kwargs in conn.writes}
        for fname in ("app.js", "styles.css", "sw.js", "icon.svg"):
            assert f"/var/www/private/pwa/{fname}" in written_paths
