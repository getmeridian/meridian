"""Tests for meridian dev preview command."""

from __future__ import annotations

import json
from pathlib import Path

from meridian.commands.dev import _build_demo_urls, run_preview


class TestBuildDemoUrls:
    def test_returns_reality_and_xhttp(self) -> None:
        urls = _build_demo_urls()
        assert len(urls) == 2
        assert urls[0].key == "reality"
        assert urls[1].key == "xhttp"

    def test_reality_url_has_expected_fields(self) -> None:
        urls = _build_demo_urls()
        url = urls[0].url
        assert "vless://" in url
        assert "security=reality" in url
        assert "198.51.100.1" in url

    def test_qr_codes_generated(self) -> None:
        urls = _build_demo_urls()
        for u in urls:
            assert u.qr_b64, f"QR missing for {u.key}"

    def test_custom_server_ip(self) -> None:
        urls = _build_demo_urls(server_ip="203.0.113.5")
        assert "203.0.113.5" in urls[0].url

    def test_no_xhttp(self) -> None:
        urls = _build_demo_urls(xhttp=False)
        assert len(urls) == 1
        assert urls[0].key == "reality"


class TestRunPreviewOutput:
    def test_output_mode_writes_files(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), client_name="test", no_open=True)

        # Shared PWA assets
        assert (output_dir / "pwa" / "app.js").is_file()
        assert (output_dir / "pwa" / "styles.css").is_file()
        assert (output_dir / "pwa" / "sw.js").is_file()
        assert (output_dir / "pwa" / "icon.svg").is_file()

        # Find client directory (UUID-named)
        client_dirs = [d for d in output_dir.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")]
        assert len(client_dirs) == 1
        client_dir = client_dirs[0]

        # Per-client files
        assert (client_dir / "index.html").is_file()
        assert (client_dir / "config.json").is_file()
        assert (client_dir / "manifest.webmanifest").is_file()
        assert not (client_dir / "sub.txt").exists()

    def test_config_json_has_demo_data(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), client_name="alice", no_open=True)

        client_dirs = [d for d in output_dir.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")]
        config = json.loads((client_dirs[0] / "config.json").read_text())
        assert config["client_name"] == "alice"
        assert config["server_ip"] == "198.51.100.1"
        assert len(config["protocols"]) == 2

    def test_mock_stats_created(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), no_open=True)

        stats_dir = output_dir / "stats"
        assert stats_dir.is_dir()
        stats_files = list(stats_dir.glob("*.json"))
        assert len(stats_files) == 1
        stats = json.loads(stats_files[0].read_text())
        assert "up" in stats
        assert "down" in stats
        assert "lastOnline" in stats

    def test_index_html_references_pwa_assets(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), no_open=True)

        client_dirs = [d for d in output_dir.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")]
        html = (client_dirs[0] / "index.html").read_text()
        assert "app.js" in html
        assert "styles.css" in html
        assert "manifest.webmanifest" in html

    def test_deterministic_uuid(self, tmp_path: Path) -> None:
        """Same client name produces same UUID directory."""
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"
        run_preview(output=str(dir1), client_name="bob", no_open=True)
        run_preview(output=str(dir2), client_name="bob", no_open=True)

        dirs1 = {d.name for d in dir1.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")}
        dirs2 = {d.name for d in dir2.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")}
        assert dirs1 == dirs2

    def test_output_writes_redirect_index(self, tmp_path: Path) -> None:
        """Static output mode writes a redirect index.html at the root."""
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), no_open=True)

        root_html = (output_dir / "index.html").read_text()
        assert 'http-equiv="refresh"' in root_html
        # Should redirect to the client UUID directory
        client_dirs = [d for d in output_dir.iterdir() if d.is_dir() and d.name not in ("pwa", "stats")]
        assert client_dirs[0].name in root_html

    def test_output_disables_service_worker(self, tmp_path: Path) -> None:
        """Static output mode replaces SW with a no-op."""
        output_dir = tmp_path / "preview"
        run_preview(output=str(output_dir), no_open=True)

        sw_content = (output_dir / "pwa" / "sw.js").read_text()
        assert "SW disabled" in sw_content
        # Should NOT contain the real SW cache logic
        assert "CACHE_VERSION" not in sw_content
