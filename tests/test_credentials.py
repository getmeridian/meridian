"""Tests for credential dataclass and YAML persistence."""

from __future__ import annotations

from pathlib import Path

from meridian.credentials import (
    ClientEntry,
    ServerCredentials,
    creds_path,
    merge_clients_file,
)


class TestServerCredentials:
    def test_load_missing_file(self, tmp_path: Path) -> None:
        creds = ServerCredentials.load(tmp_path / "nonexistent.yml")
        assert creds.panel.username is None
        assert creds.has_credentials is False

    def test_load_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yml"
        f.write_text("")
        creds = ServerCredentials.load(f)
        assert creds.panel.username is None

    def test_load_v2_sample(self, sample_proxy_yml: Path) -> None:
        creds = ServerCredentials.load(sample_proxy_yml)
        assert creds.version == 2
        assert creds.panel.username == "admin"
        assert creds.panel.password == "s3cret!pass"
        assert creds.panel.web_base_path == "abc123"
        assert creds.panel.info_page_path == "info456"
        assert creds.panel.port == 2053
        assert creds.server.sni == "www.cloudflare.com"
        assert creds.reality.uuid == "550e8400-e29b-41d4-a716-446655440000"
        assert creds.reality.private_key == "WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo"
        assert creds.reality.public_key == "K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy"
        assert creds.reality.short_id == "abcd1234"
        assert creds.wss.uuid == "660e8400-e29b-41d4-a716-446655440001"
        assert creds.wss.ws_path == "ws789"
        assert creds.server.domain is None
        assert creds.has_credentials is True
        assert creds.has_domain is False
        assert len(creds.clients) == 1
        assert creds.clients[0].name == "default"

    def test_load_v2_with_domain(self, tmp_path: Path) -> None:
        f = tmp_path / "proxy.yml"
        f.write_text("version: 2\npanel:\n  username: admin\n  password: pass\nserver:\n  domain: example.com\n")
        creds = ServerCredentials.load(f)
        assert creds.has_domain is True
        assert creds.server.domain == "example.com"

    def test_load_v1_migration(self, sample_v1_proxy_yml: Path) -> None:
        """V1 flat format should be auto-migrated to v2."""
        creds = ServerCredentials.load(sample_v1_proxy_yml)
        assert creds.version == 2
        assert creds.panel.username == "admin"
        assert creds.panel.password == "s3cret!pass"
        assert creds.panel.web_base_path == "abc123"
        assert creds.panel.info_page_path == "info456"
        assert creds.server.sni == "www.cloudflare.com"
        assert creds.server.ip == "1.2.3.4"
        assert creds.server.scanned_sni == "dl.google.com"
        assert creds.reality.uuid == "550e8400-e29b-41d4-a716-446655440000"
        assert creds.reality.private_key == "WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo"
        assert creds.reality.public_key == "K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy"
        assert creds.reality.short_id == "abcd1234"
        assert creds.wss.uuid == "660e8400-e29b-41d4-a716-446655440001"
        assert creds.wss.ws_path == "ws789"
        assert creds.has_credentials is True

    def test_v1_save_writes_v2(self, sample_v1_proxy_yml: Path) -> None:
        """Loading v1 and saving should produce v2 format."""
        creds = ServerCredentials.load(sample_v1_proxy_yml)
        creds.save(sample_v1_proxy_yml)

        # Re-load and verify v2 format
        import yaml

        data = yaml.safe_load(sample_v1_proxy_yml.read_text())
        assert data["version"] == 2
        assert data["panel"]["username"] == "admin"
        assert data["protocols"]["reality"]["uuid"] == "550e8400-e29b-41d4-a716-446655440000"

    def test_load_ignores_unknown_fields(self, tmp_path: Path) -> None:
        f = tmp_path / "proxy.yml"
        f.write_text("version: 2\npanel:\n  username: admin\nfuture_field: value\nanother: 42\n")
        creds = ServerCredentials.load(f)
        assert creds.panel.username == "admin"
        # Unknown fields should be preserved in _extra
        assert creds._extra.get("future_field") == "value"

    def test_save_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "subdir" / "proxy.yml"
        creds = ServerCredentials()
        creds.panel.username = "admin"
        creds.panel.password = "pass"
        creds.save(path)
        assert path.exists()
        assert oct(path.stat().st_mode)[-3:] == "600"

        loaded = ServerCredentials.load(path)
        assert loaded.panel.username == "admin"
        assert loaded.panel.password == "pass"

    def test_save_preserves_unknown_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "proxy.yml"
        path.write_text("version: 2\npanel:\n  username: old\nfuture_field: keep_me\n")
        path.chmod(0o600)

        creds = ServerCredentials.load(path)
        creds.panel.username = "new"
        creds.panel.password = "pass"
        creds.save(path)

        import yaml

        data = yaml.safe_load(path.read_text())
        assert data["panel"]["username"] == "new"
        assert data["future_field"] == "keep_me"

    def test_save_preserves_unknown_nested_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "proxy.yml"
        path.write_text(
            "version: 2\n"
            "panel:\n"
            "  username: old\n"
            "  future_panel_flag: keep_panel\n"
            "server:\n"
            "  ip: 1.2.3.4\n"
            "  future_server_flag: keep_server\n"
            "protocols:\n"
            "  reality:\n"
            "    uuid: old-uuid\n"
            "    future_reality_flag: keep_reality\n"
            "clients:\n"
            "  - name: alice\n"
            "    reality_uuid: old-uuid\n"
            "    future_client_flag: keep_client\n"
            "relays:\n"
            "  - ip: 198.51.100.2\n"
            "    future_relay_flag: keep_relay\n"
            "branding:\n"
            "  server_name: Test\n"
            "  future_branding_flag: keep_branding\n"
        )

        creds = ServerCredentials.load(path)
        creds.panel.username = "new"
        creds.reality.uuid = "new-uuid"
        creds.clients[0].name = "alice-renamed"
        creds.relays[0].name = "relay-1"
        creds.branding.color = "ocean"
        creds.save(path)

        import yaml

        data = yaml.safe_load(path.read_text())
        assert data["panel"]["future_panel_flag"] == "keep_panel"
        assert data["server"]["future_server_flag"] == "keep_server"
        assert data["protocols"]["reality"]["future_reality_flag"] == "keep_reality"
        assert data["clients"][0]["future_client_flag"] == "keep_client"
        assert data["relays"][0]["future_relay_flag"] == "keep_relay"
        assert data["branding"]["future_branding_flag"] == "keep_branding"

    def test_save_preserves_falsy_unknown_branding_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "proxy.yml"
        path.write_text(
            "version: 2\n"
            "branding:\n"
            "  server_name: Test\n"
            "  future_branding_bool: false\n"
            "  future_branding_zero: 0\n"
            "  future_branding_list: []\n"
        )

        creds = ServerCredentials.load(path)
        creds.branding.color = "ocean"
        creds.save(path)

        import yaml

        data = yaml.safe_load(path.read_text())
        assert data["branding"]["future_branding_bool"] is False
        assert data["branding"]["future_branding_zero"] == 0
        assert data["branding"]["future_branding_list"] == []

    def test_save_preserves_unknown_protocol_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "proxy.yml"
        path.write_text(
            "version: 2\n"
            "panel:\n"
            "  username: admin\n"
            "protocols:\n"
            "  reality:\n"
            "    uuid: known-uuid\n"
            "  futureproto:\n"
            "    token: keep_me\n"
            "    enabled: true\n"
        )

        creds = ServerCredentials.load(path)
        creds.panel.password = "secret"
        creds.save(path)

        import yaml

        data = yaml.safe_load(path.read_text())
        assert data["protocols"]["futureproto"]["token"] == "keep_me"
        assert data["protocols"]["futureproto"]["enabled"] is True

    def test_save_none_not_written(self, tmp_path: Path) -> None:
        """None values should not appear in the output."""
        path = tmp_path / "proxy.yml"
        creds = ServerCredentials()
        creds.panel.username = "admin"
        # domain is None by default
        creds.save(path)

        import yaml

        data = yaml.safe_load(path.read_text())
        server_data = data.get("server", {})
        assert "domain" not in server_data

    def test_load_handles_none_values(self, tmp_path: Path) -> None:
        f = tmp_path / "proxy.yml"
        f.write_text("version: 2\npanel:\n  username: admin\nserver:\n  sni:\n")
        creds = ServerCredentials.load(f)
        assert creds.panel.username == "admin"
        assert creds.server.sni is None  # YAML null → None

    def test_load_special_chars_in_password(self, tmp_path: Path) -> None:
        """This is the case that broke the bash grep/awk approach."""
        f = tmp_path / "proxy.yml"
        f.write_text('version: 2\npanel:\n  username: admin\n  password: "p@ss: with spaces & special!"\n')
        creds = ServerCredentials.load(f)
        assert creds.panel.password == "p@ss: with spaces & special!"

    def test_reality_property_creates_default(self) -> None:
        creds = ServerCredentials()
        assert creds.reality.uuid is None
        # Should have created the protocol entry
        assert "reality" in creds.protocols

    def test_wss_property_creates_default(self) -> None:
        creds = ServerCredentials()
        assert creds.wss.uuid is None
        assert "wss" in creds.protocols

    def test_xhttp_property_creates_default(self) -> None:
        creds = ServerCredentials()
        assert creds.xhttp.uuid is None
        assert "xhttp" in creds.protocols


class TestV1Migration:
    def test_v1_exit_ip_becomes_server_ip(self, tmp_path: Path) -> None:
        """V1 exit_ip field maps to server.ip in v2."""
        f = tmp_path / "proxy.yml"
        f.write_text("exit_ip: 1.2.3.4\npanel_username: admin\npanel_password: pass\n")
        creds = ServerCredentials.load(f)
        assert creds.server.ip == "1.2.3.4"

    def test_v1_server_ip_becomes_server_ip(self, tmp_path: Path) -> None:
        """V1 server_ip field maps to server.ip in v2."""
        f = tmp_path / "proxy.yml"
        f.write_text("server_ip: 5.6.7.8\npanel_username: admin\npanel_password: pass\n")
        creds = ServerCredentials.load(f)
        assert creds.server.ip == "5.6.7.8"

    def test_v1_xhttp_enabled_skipped(self, tmp_path: Path) -> None:
        """V1 xhttp_enabled is consumed but not carried forward to v2."""
        f = tmp_path / "proxy.yml"
        f.write_text("panel_username: admin\nxhttp_enabled: true\n")
        creds = ServerCredentials.load(f)
        # xhttp_enabled is not a v2 field — it's runtime-only
        assert "xhttp_enabled" not in creds._extra

    def test_v1_panel_configured_preserved(self, tmp_path: Path) -> None:
        """V1 panel_configured is preserved in _extra for backward compatibility."""
        f = tmp_path / "proxy.yml"
        f.write_text("panel_username: admin\npanel_configured: true\n")
        creds = ServerCredentials.load(f)
        assert creds._extra["panel_configured"] is True

    def test_panel_configured_round_trip(self, tmp_path: Path) -> None:
        """panel_configured survives load-save-load round trip."""
        f = tmp_path / "proxy.yml"
        f.write_text("panel_username: admin\npanel_configured: true\n")
        creds = ServerCredentials.load(f)
        creds.save(f)
        creds2 = ServerCredentials.load(f)
        assert creds2._extra["panel_configured"] is True
        assert creds2.panel.username == "admin"


class TestMergeClientsFile:
    def test_merge_clients(self, tmp_path: Path) -> None:
        clients_file = tmp_path / "proxy-clients.yml"
        clients_file.write_text(
            "---\n"
            "clients:\n"
            '  - name: "alice"\n'
            '    added: "2026-01-01T00:00:00Z"\n'
            '    reality_uuid: "uuid-1"\n'
            '    wss_uuid: "uuid-2"\n'
        )
        creds = ServerCredentials()
        result = merge_clients_file(creds, clients_file)
        assert result is True
        assert len(creds.clients) == 1
        assert creds.clients[0].name == "alice"
        assert creds.clients[0].reality_uuid == "uuid-1"

    def test_merge_deduplicates(self, tmp_path: Path) -> None:
        clients_file = tmp_path / "proxy-clients.yml"
        clients_file.write_text(
            '---\nclients:\n  - name: "alice"\n    added: "2026-01-01T00:00:00Z"\n    reality_uuid: "uuid-1"\n'
        )
        creds = ServerCredentials()
        creds.clients.append(ClientEntry(name="alice", added="2025-12-01", reality_uuid="old-uuid"))
        result = merge_clients_file(creds, clients_file)
        assert result is True
        # Should not duplicate
        assert len(creds.clients) == 1
        assert creds.clients[0].reality_uuid == "old-uuid"  # original preserved

    def test_merge_missing_file(self, tmp_path: Path) -> None:
        creds = ServerCredentials()
        result = merge_clients_file(creds, tmp_path / "nonexistent.yml")
        assert result is False
        assert len(creds.clients) == 0


class TestDeployedWith:
    def test_deployed_with_default_empty(self) -> None:
        creds = ServerCredentials()
        assert creds.server.deployed_with == ""

    def test_deployed_with_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "proxy.yml"
        creds = ServerCredentials()
        creds.panel.username = "admin"
        creds.panel.password = "pass"
        creds.server.deployed_with = "3.5.0"
        creds.save(path)

        loaded = ServerCredentials.load(path)
        assert loaded.server.deployed_with == "3.5.0"

    def test_deployed_with_missing_in_yaml(self, tmp_path: Path) -> None:
        """Legacy credentials without deployed_with should load with empty string."""
        f = tmp_path / "proxy.yml"
        f.write_text("version: 2\npanel:\n  username: admin\nserver:\n  ip: 1.2.3.4\n")
        creds = ServerCredentials.load(f)
        assert creds.server.deployed_with == ""

    def test_deployed_with_in_v2_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "proxy.yml"
        f.write_text("version: 2\npanel:\n  username: admin\nserver:\n  ip: 1.2.3.4\n  deployed_with: '4.0.1'\n")
        creds = ServerCredentials.load(f)
        assert creds.server.deployed_with == "4.0.1"


class TestCredsPath:
    def test_creds_path(self, tmp_path: Path) -> None:
        result = creds_path(tmp_path, "1.2.3.4")
        assert result == tmp_path / "1.2.3.4" / "proxy.yml"

    def test_creds_path_ipv6_sanitized(self, tmp_path: Path) -> None:
        result = creds_path(tmp_path, "2001:db8::1")
        assert result == tmp_path / "2001-db8--1" / "proxy.yml"

    def test_creds_path_ipv4_unchanged(self, tmp_path: Path) -> None:
        result = creds_path(tmp_path, "198.51.100.1")
        assert result == tmp_path / "198.51.100.1" / "proxy.yml"
