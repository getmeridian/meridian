"""Tests for relay node functionality."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meridian.credentials import RelayEntry, ServerCredentials
from meridian.models import ProtocolURL, RelayURLSet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_proxy_with_relays(creds_dir: Path) -> Path:
    """Write a v2 proxy.yml with relay entries."""
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
  ip: 5.6.7.8
  sni: www.microsoft.com
  hosted_page: true
protocols:
  reality:
    uuid: 550e8400-e29b-41d4-a716-446655440000
    private_key: WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo
    public_key: K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy
    short_id: abcd1234
  wss:
    uuid: 660e8400-e29b-41d4-a716-446655440001
    ws_path: ws789
  xhttp:
    xhttp_path: xhttp123
clients:
  - name: default
    added: "2026-01-01T00:00:00Z"
    reality_uuid: 550e8400-e29b-41d4-a716-446655440000
    wss_uuid: 660e8400-e29b-41d4-a716-446655440001
relays:
  - ip: 1.2.3.4
    name: ru-moscow
    port: 443
    added: "2026-03-22T12:00:00Z"
    sni: yandex.ru
  - ip: 10.20.30.40
    name: ru-spb
    port: 443
    added: "2026-03-22T13:00:00Z"
"""
    proxy = creds_dir / "proxy.yml"
    proxy.write_text(content)
    proxy.chmod(0o600)
    return proxy


# ---------------------------------------------------------------------------
# Credential tests
# ---------------------------------------------------------------------------


class TestRelayEntry:
    def test_relay_entry_defaults(self) -> None:
        entry = RelayEntry()
        assert entry.ip == ""
        assert entry.name == ""
        assert entry.port == 443
        assert entry.added == ""
        assert entry.sni == ""

    def test_relay_entry_with_values(self) -> None:
        entry = RelayEntry(ip="1.2.3.4", name="ru-moscow", port=443, added="2026-01-01T00:00:00Z", sni="yandex.ru")
        assert entry.ip == "1.2.3.4"
        assert entry.name == "ru-moscow"
        assert entry.port == 443
        assert entry.sni == "yandex.ru"


class TestCredentialsWithRelays:
    def test_load_with_relays(self, sample_proxy_with_relays: Path) -> None:
        creds = ServerCredentials.load(sample_proxy_with_relays)
        assert len(creds.relays) == 2
        assert creds.relays[0].ip == "1.2.3.4"
        assert creds.relays[0].name == "ru-moscow"
        assert creds.relays[0].sni == "yandex.ru"
        assert creds.relays[1].ip == "10.20.30.40"
        assert creds.relays[1].name == "ru-spb"
        assert creds.relays[1].sni == ""  # no SNI = legacy behavior

    def test_load_without_relays(self, sample_proxy_yml: Path) -> None:
        """Old credentials without relays should have empty relay list."""
        creds = ServerCredentials.load(sample_proxy_yml)
        assert creds.relays == []

    def test_save_and_load_relays_roundtrip(self, tmp_path: Path) -> None:
        creds = ServerCredentials()
        creds.relays = [
            RelayEntry(ip="1.2.3.4", name="test-relay", port=443, added="2026-01-01T00:00:00Z"),
        ]
        path = tmp_path / "proxy.yml"
        creds.save(path)

        loaded = ServerCredentials.load(path)
        assert len(loaded.relays) == 1
        assert loaded.relays[0].ip == "1.2.3.4"
        assert loaded.relays[0].name == "test-relay"
        assert loaded.relays[0].port == 443

    def test_add_and_remove_relay(self) -> None:
        creds = ServerCredentials()
        relay = RelayEntry(ip="1.2.3.4", name="relay1")
        creds.relays.append(relay)
        assert len(creds.relays) == 1

        creds.relays = [r for r in creds.relays if r.ip != "1.2.3.4"]
        assert len(creds.relays) == 0

    def test_save_without_relays_omits_section(self, tmp_path: Path) -> None:
        """Credentials with no relays should not have a relays key in YAML."""
        creds = ServerCredentials()
        path = tmp_path / "proxy.yml"
        creds.save(path)

        raw = path.read_text()
        assert "relays" not in raw


# ---------------------------------------------------------------------------
# URL generation tests
# ---------------------------------------------------------------------------


class TestBuildRelayUrls:
    def test_build_relay_urls(self, sample_proxy_with_relays: Path) -> None:
        from meridian.urls import build_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        result = build_relay_urls("alice", uuid, wss_uuid, creds, "1.2.3.4", "ru-moscow")

        assert isinstance(result, RelayURLSet)
        assert result.relay_ip == "1.2.3.4"
        assert result.relay_name == "ru-moscow"
        # Should have Reality + XHTTP + WSS (since sample has domain='' but xhttp_path set)
        assert len(result.urls) >= 1

        # Reality URL should contain relay IP, not exit IP
        reality_url = next(u for u in result.urls if u.key == "reality")
        assert "@1.2.3.4:" in reality_url.url
        assert "@5.6.7.8:" not in reality_url.url

        # URL should contain exit's Reality parameters
        assert "K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy" in reality_url.url  # public key
        assert "abcd1234" in reality_url.url  # short ID
        # Without relay_sni, Reality SNI defaults to exit's SNI
        assert "www.microsoft.com" in reality_url.url
        assert uuid in reality_url.url

        # Fragment should include relay identifier
        assert "via-ru-moscow" in reality_url.url

        # XHTTP URL should exist (xhttp_path is set in fixture)
        xhttp_urls = [u for u in result.urls if u.key == "xhttp"]
        assert len(xhttp_urls) == 1
        xhttp_url = xhttp_urls[0].url
        assert "@1.2.3.4:" in xhttp_url  # connects to relay
        assert "sni=5.6.7.8" in xhttp_url  # no relay SNI, no domain → falls back to exit IP
        assert "fp=chrome" in xhttp_url  # TLS fingerprint
        assert "xhttp123" in xhttp_url  # path preserved

    def test_build_relay_urls_with_relay_sni(self, sample_proxy_with_relays: Path) -> None:
        """When relay_sni is set, Reality URL uses it instead of exit's SNI."""
        from meridian.urls import build_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        result = build_relay_urls(
            "alice",
            uuid,
            wss_uuid,
            creds,
            "1.2.3.4",
            "ru-moscow",
            relay_sni="yandex.ru",
        )
        reality_url = next(u for u in result.urls if u.key == "reality")

        # Reality SNI should be relay-specific
        assert "sni=yandex.ru" in reality_url.url
        assert "sni=www.microsoft.com" not in reality_url.url

        # XHTTP should also use relay SNI
        xhttp_urls = [u for u in result.urls if u.key == "xhttp"]
        if xhttp_urls:
            assert "sni=yandex.ru" in xhttp_urls[0].url

    def test_build_all_relay_urls_uses_relay_sni(self, sample_proxy_with_relays: Path) -> None:
        """build_all_relay_urls passes relay.sni from each RelayEntry."""
        from meridian.urls import build_all_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        results = build_all_relay_urls("alice", uuid, wss_uuid, creds)
        assert len(results) == 2

        # First relay has sni=yandex.ru → Reality URL uses it
        r0_reality = next(u for u in results[0].urls if u.key == "reality")
        assert "sni=yandex.ru" in r0_reality.url

        # Second relay has no sni → Reality URL uses exit's default
        r1_reality = next(u for u in results[1].urls if u.key == "reality")
        assert "sni=www.microsoft.com" in r1_reality.url

    def test_build_relay_urls_no_name(self, sample_proxy_with_relays: Path) -> None:
        from meridian.urls import build_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        result = build_relay_urls("bob", uuid, wss_uuid, creds, "9.9.9.9")
        assert "via-9.9.9.9" in result.urls[0].url

    def test_build_relay_urls_custom_port(self, sample_proxy_with_relays: Path) -> None:
        from meridian.urls import build_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        result = build_relay_urls("alice", uuid, wss_uuid, creds, "1.2.3.4", "test", relay_port=9443)

        # All URLs must use port 9443, not 443
        for purl in result.urls:
            assert "@1.2.3.4:9443" in purl.url, f"{purl.key} URL has wrong port: {purl.url}"
            assert ":443" not in purl.url.split("@")[1].split("?")[0], f"{purl.key} URL still has :443"

    def test_build_all_relay_urls(self, sample_proxy_with_relays: Path) -> None:
        from meridian.urls import build_all_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        results = build_all_relay_urls("alice", uuid, wss_uuid, creds)
        assert len(results) == 2
        assert results[0].relay_ip == "1.2.3.4"
        assert results[1].relay_ip == "10.20.30.40"

    def test_build_all_relay_urls_no_relays(self, sample_proxy_yml: Path) -> None:
        from meridian.urls import build_all_relay_urls

        creds = ServerCredentials.load(sample_proxy_yml)
        uuid = "550e8400-e29b-41d4-a716-446655440000"

        results = build_all_relay_urls("alice", uuid, "", creds)
        assert results == []

    def test_build_relay_urls_with_server_name(self, sample_proxy_with_relays: Path) -> None:
        from meridian.urls import build_relay_urls

        creds = ServerCredentials.load(sample_proxy_with_relays)
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        wss_uuid = "660e8400-e29b-41d4-a716-446655440001"

        result = build_relay_urls(
            "alice",
            uuid,
            wss_uuid,
            creds,
            "1.2.3.4",
            "ru-moscow",
            server_name="My VPN",
        )
        reality_url = next(u for u in result.urls if u.key == "reality")
        assert "#alice @ My VPN-via-ru-moscow" in reality_url.url


# ---------------------------------------------------------------------------
# RelayURLSet model tests
# ---------------------------------------------------------------------------


class TestRelayURLSet:
    def test_frozen(self) -> None:
        url_set = RelayURLSet(
            relay_ip="1.2.3.4",
            relay_name="test",
            urls=[ProtocolURL(key="reality", label="Primary", url="vless://test@1.2.3.4:443")],
        )
        assert url_set.relay_ip == "1.2.3.4"
        assert url_set.relay_name == "test"
        assert len(url_set.urls) == 1


# ---------------------------------------------------------------------------
# Provisioner tests
# ---------------------------------------------------------------------------


class TestRelayProvisioner:
    def test_build_relay_steps(self) -> None:
        from meridian.provision.relay import RelayContext, build_relay_steps

        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")
        steps = build_relay_steps(ctx)
        assert len(steps) == 6
        names = [s.name for s in steps]
        assert "Install system packages" in names
        assert "Enable BBR congestion control" in names
        assert "Configure relay firewall" in names
        assert "Install Realm TCP relay" in names
        assert "Configure Realm relay" in names
        assert "Verify relay connectivity" in names

    def test_relay_context_defaults(self) -> None:
        from meridian.provision.relay import RelayContext

        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")
        assert ctx.exit_port == 443
        assert ctx.listen_port == 443
        assert ctx.user == "root"

    def test_configure_realm_generates_correct_config(self) -> None:
        """Verify ConfigureRealm generates valid TOML-like config content."""
        from meridian.provision.relay import ConfigureRealm, RelayContext

        step = ConfigureRealm()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8", listen_port=443)

        # Mock ServerConnection
        conn = MagicMock()
        conn.run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        conn.put_text = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        result = step.run(conn, ctx)
        assert result.status == "changed"

        # Check that config was written with correct exit IP and port
        calls = [str(c) for c in conn.put_text.call_args_list]
        config_call = [c for c in calls if "realm.toml" in c]
        assert len(config_call) > 0

    def test_verify_relay_checks_service_and_tcp(self) -> None:
        from meridian.provision.relay import RelayContext, VerifyRelay

        step = VerifyRelay()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")

        # Mock: service active, TCP reachable
        conn = MagicMock()
        conn.run = MagicMock(
            side_effect=[
                MagicMock(returncode=0, stdout="active\n", stderr=""),  # systemctl is-active
                MagicMock(returncode=0, stdout="", stderr=""),  # TCP test
            ]
        )

        result = step.run(conn, ctx)
        assert result.status == "ok"

    def test_verify_relay_fails_on_inactive_service(self) -> None:
        from meridian.provision.relay import RelayContext, VerifyRelay

        step = VerifyRelay()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")

        conn = MagicMock()
        conn.run = MagicMock(
            side_effect=[
                # 4 retries of systemctl is-active, all inactive
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),  # journalctl
            ]
        )

        with patch("meridian.provision.relay.time.sleep"):
            result = step.run(conn, ctx)
        assert result.status == "failed"

    def test_verify_relay_retries_until_active(self) -> None:
        """Service becomes active on third attempt — should succeed."""
        from meridian.provision.relay import RelayContext, VerifyRelay

        step = VerifyRelay()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")

        conn = MagicMock()
        conn.run = MagicMock(
            side_effect=[
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),  # attempt 1
                MagicMock(returncode=3, stdout="inactive\n", stderr=""),  # attempt 2
                MagicMock(returncode=0, stdout="active\n", stderr=""),  # attempt 3 — success
                MagicMock(returncode=0, stdout="", stderr=""),  # TCP test
            ]
        )

        with patch("meridian.provision.relay.time.sleep") as mock_sleep:
            result = step.run(conn, ctx)
        assert result.status == "ok"
        assert mock_sleep.call_count == 2  # slept before attempt 2 and 3

    def test_install_realm_verifies_checksum(self) -> None:
        """InstallRealm should reject a binary with mismatched SHA256."""
        from meridian.provision.relay import InstallRealm, RelayContext

        step = InstallRealm()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")

        conn = MagicMock()

        def mock_run(cmd: str, timeout: int = 30, **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "uname -m" in cmd:
                result.stdout = "x86_64\n"
            elif "realm --version" in cmd:
                # Not installed yet
                result.returncode = 1
                result.stdout = ""
            elif "sha256sum" in cmd:
                # Return a bad hash
                result.stdout = "deadbeef00000000000000000000000000000000000000000000000000000000\n"
            elif "rm -f" in cmd:
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        conn.run = MagicMock(side_effect=mock_run)
        result = step.run(conn, ctx)
        assert result.status == "failed"
        assert "checksum mismatch" in result.detail

    def test_install_realm_passes_correct_checksum(self) -> None:
        """InstallRealm should succeed when SHA256 matches."""
        from meridian.config import REALM_SHA256
        from meridian.provision.relay import InstallRealm, RelayContext

        step = InstallRealm()
        ctx = RelayContext(relay_ip="1.2.3.4", exit_ip="5.6.7.8")
        expected_hash = REALM_SHA256["x86_64-unknown-linux-gnu"]
        version_calls = []

        conn = MagicMock()

        def mock_run(cmd: str, timeout: int = 30, **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "uname -m" in cmd:
                result.stdout = "x86_64\n"
            elif "realm --version" in cmd:
                version_calls.append(cmd)
                if len(version_calls) == 1:
                    # First check: not installed yet
                    result.returncode = 1
                    result.stdout = ""
                else:
                    # Post-install verification
                    result.stdout = f"realm {ctx.realm_version}\n"
            elif "sha256sum" in cmd:
                # Mock returns just the hash (as if cut -d' ' -f1 ran)
                result.stdout = f"{expected_hash}\n"
            else:
                result.stdout = ""
            return result

        conn.run = MagicMock(side_effect=mock_run)
        result = step.run(conn, ctx)
        assert result.status == "changed"


class TestRelayCLI:
    def test_relay_help(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["relay", "--help"])
        assert result.exit_code == 0
        assert "deploy" in result.output
        assert "list" in result.output
        assert "remove" in result.output
        assert "check" in result.output

    def test_relay_deploy_help(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["relay", "deploy", "--help"])
        assert result.exit_code == 0
        assert "RELAY_IP" in result.output
        assert "Exit server" in result.output


# ---------------------------------------------------------------------------
# Rendering tests
# ---------------------------------------------------------------------------


class TestRenderingWithRelays:
    def test_save_connection_html_with_relays_fallback(self, tmp_path: Path) -> None:
        """Test HTML output with relay entries includes relay section."""
        from meridian.render import save_connection_html

        protocol_urls = [
            ProtocolURL(key="reality", label="Primary", url="vless://uuid@5.6.7.8:443?security=reality#alice"),
        ]
        relay_entries = [
            RelayURLSet(
                relay_ip="1.2.3.4",
                relay_name="ru-moscow",
                urls=[
                    ProtocolURL(
                        key="reality",
                        label="Primary (via relay)",
                        url="vless://uuid@1.2.3.4:443?security=reality#alice-via-ru-moscow",
                    )
                ],
            ),
        ]

        dest = tmp_path / "test.html"
        save_connection_html(protocol_urls, dest, "5.6.7.8", relay_entries=relay_entries)

        content = dest.read_text()
        # Relay section should be present
        assert "BACKUP" in content
        assert "1.2.3.4" in content
        assert "ru-moscow" in content
        # Direct Primary card should NOT say "Recommended" when relays exist
        assert "Recommended — fastest" not in content

    def test_save_connection_html_without_relays_unchanged(self, tmp_path: Path) -> None:
        """Without relays, HTML output should not contain relay sections."""
        from meridian.render import save_connection_html

        protocol_urls = [
            ProtocolURL(key="reality", label="Primary", url="vless://uuid@5.6.7.8:443?security=reality#alice"),
        ]

        dest = tmp_path / "test.html"
        save_connection_html(protocol_urls, dest, "5.6.7.8")

        content = dest.read_text()
        assert "BACKUP (DIRECT)" not in content
        assert "via relay" not in content


# ---------------------------------------------------------------------------
# Nginx stream config tests
# ---------------------------------------------------------------------------


class TestNginxStreamRelay:
    def test_stream_config_includes_relay_maps(self) -> None:
        """Main stream config should include relay-maps directory."""
        from meridian.provision.nginx import _render_nginx_stream_config

        config = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="5.6.7.8",
        )
        assert "include /etc/nginx/stream.d/relay-maps/*.conf;" in config


# ---------------------------------------------------------------------------
# Relay helper tests
# ---------------------------------------------------------------------------


class TestRelayHelpers:
    def test_relay_label_from_name(self) -> None:
        from meridian.relay_ops import relay_label

        entry = RelayEntry(ip="1.2.3.4", name="ru-moscow")
        assert relay_label(entry) == "ru-moscow"

    def test_relay_label_from_ip(self) -> None:
        from meridian.relay_ops import relay_label

        entry = RelayEntry(ip="1.2.3.4")
        assert relay_label(entry) == "1-2-3-4"

    def test_relay_xray_port_deterministic(self) -> None:
        from meridian.relay_ops import relay_xray_port

        port = relay_xray_port("1.2.3.4")
        assert 40000 <= port <= 49999
        assert relay_xray_port("1.2.3.4") == port  # same input → same output

    def test_relay_xray_port_differs_per_ip(self) -> None:
        from meridian.relay_ops import relay_xray_port

        assert relay_xray_port("1.2.3.4") != relay_xray_port("5.6.7.8")


# ---------------------------------------------------------------------------
# Relay SNI serialization roundtrip
# ---------------------------------------------------------------------------


class TestRelaySNISerialization:
    def test_save_load_roundtrip_with_sni(self, tmp_path: Path) -> None:
        creds = ServerCredentials()
        creds.relays = [
            RelayEntry(ip="1.2.3.4", name="ru", port=443, sni="yandex.ru"),
            RelayEntry(ip="5.6.7.8", name="legacy", port=443),
        ]
        path = tmp_path / "proxy.yml"
        creds.save(path)
        loaded = ServerCredentials.load(path)
        assert loaded.relays[0].sni == "yandex.ru"
        assert loaded.relays[1].sni == ""

    def test_legacy_yaml_without_sni_defaults_empty(self, tmp_path: Path) -> None:
        """YAML without sni field should load with sni='' (backward compat)."""
        path = tmp_path / "proxy.yml"
        path.write_text("""\
version: 2
relays:
  - ip: 1.2.3.4
    name: test
    port: 443
""")
        creds = ServerCredentials.load(path)
        assert creds.relays[0].sni == ""
