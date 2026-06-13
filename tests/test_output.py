"""Tests for URL building, QR generation, and connection file output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from meridian.credentials import (
    PanelConfig,
    RealityConfig,
    ServerConfig,
    ServerCredentials,
    WSSConfig,
    XHTTPConfig,
)
from meridian.models import ProtocolURL
from meridian.render import save_connection_html
from meridian.urls import build_protocol_urls, generate_qr_base64, generate_qr_terminal


def _make_creds(
    ip: str = "1.2.3.4",
    sni: str = "www.microsoft.com",
    public_key: str = "K6JYbz4MflVPaaxdtRHo",
    short_id: str = "abcd1234",
    domain: str = "",
    ws_path: str = "",
    xhttp_path: str = "",
) -> ServerCredentials:
    """Create test credentials."""
    creds = ServerCredentials(
        panel=PanelConfig(username="admin", password="pass", port=2053),
        server=ServerConfig(ip=ip, sni=sni, domain=domain or None),
        protocols={
            "reality": RealityConfig(
                uuid="base-uuid",
                public_key=public_key,
                short_id=short_id,
                private_key="private",
            ),
        },
    )
    if ws_path:
        creds.protocols["wss"] = WSSConfig(uuid="wss-uuid", ws_path=ws_path)
    if xhttp_path:
        creds.protocols["xhttp"] = XHTTPConfig(xhttp_path=xhttp_path)
    return creds


def _find_url(urls: list[ProtocolURL], key: str) -> str:
    """Find a URL by protocol key, return empty string if not found."""
    return next((p.url for p in urls if p.key == key), "")


class TestBuildProtocolURLs:
    def test_reality_url_basic(self) -> None:
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds)

        reality = _find_url(urls, "reality")
        assert reality.startswith("vless://uuid-1@1.2.3.4:443")
        assert "flow=xtls-rprx-vision" in reality
        assert "security=reality" in reality
        assert "sni=www.microsoft.com" in reality
        assert "fp=chrome" in reality
        assert "pbk=K6JYbz4MflVPaaxdtRHo" in reality
        assert "sid=abcd1234" in reality
        assert "type=tcp" in reality
        assert "#alice" in reality

    def test_no_xhttp_when_no_path(self) -> None:
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        assert _find_url(urls, "xhttp") == ""

    def test_xhttp_url_with_path(self) -> None:
        creds = _make_creds(xhttp_path="myxhttp")
        urls = build_protocol_urls("alice", "uuid-1", "", creds)

        xhttp = _find_url(urls, "xhttp")
        assert xhttp.startswith("vless://uuid-1@1.2.3.4:443")
        assert "type=xhttp" in xhttp
        assert "path=%2Fmyxhttp" in xhttp
        assert "#alice-XHTTP" in xhttp
        # XHTTP now uses TLS (via nginx), not Reality
        assert "security=tls" in xhttp
        assert "sni=1.2.3.4" in xhttp
        assert "fp=chrome" in xhttp
        # XHTTP must NOT have flow (empty)
        assert "flow=" not in xhttp

    def test_no_wss_without_domain(self) -> None:
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        assert _find_url(urls, "wss") == ""

    def test_no_wss_without_wss_uuid(self) -> None:
        creds = _make_creds(domain="example.com", ws_path="ws789")
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        assert _find_url(urls, "wss") == ""

    def test_wss_url_with_domain(self) -> None:
        creds = _make_creds(domain="example.com", ws_path="ws789")
        urls = build_protocol_urls("alice", "uuid-1", "wss-uuid", creds)

        wss = _find_url(urls, "wss")
        assert wss.startswith("vless://wss-uuid@example.com:443")
        assert "security=tls" in wss
        assert "type=ws" in wss
        assert "path=%2Fws789" in wss
        assert "#alice-WSS" in wss

    def test_reality_uuid_used_for_xhttp(self) -> None:
        """XHTTP shares the Reality UUID, not a separate one."""
        creds = _make_creds(xhttp_path="xp")
        urls = build_protocol_urls("alice", "reality-uuid", "wss-uuid", creds)
        xhttp = _find_url(urls, "xhttp")
        assert xhttp.startswith("vless://reality-uuid@")

    def test_different_sni(self) -> None:
        creds = _make_creds(sni="dl.google.com")
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "sni=dl.google.com" in reality

    def test_url_fragment_encoding(self) -> None:
        """Client name with special chars should work in URL fragment."""
        creds = _make_creds()
        urls = build_protocol_urls("my-client_1", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "#my-client_1" in reality

    def test_returns_protocol_url_list(self) -> None:
        """build_protocol_urls returns list[ProtocolURL]."""
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        assert isinstance(urls, list)
        assert all(isinstance(u, ProtocolURL) for u in urls)

    def test_server_name_in_reality_fragment(self) -> None:
        """Server name appears in URL fragment when set."""
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds, server_name="My VPN")
        reality = _find_url(urls, "reality")
        assert "#alice @ My VPN" in reality

    def test_server_name_in_all_protocol_fragments(self) -> None:
        """Server name appears in all protocol URL fragments."""
        creds = _make_creds(domain="example.com", ws_path="ws", xhttp_path="xp")
        urls = build_protocol_urls("alice", "r-uuid", "w-uuid", creds, server_name="My VPN")
        reality = _find_url(urls, "reality")
        xhttp = _find_url(urls, "xhttp")
        wss = _find_url(urls, "wss")
        assert "#alice @ My VPN" in reality
        assert "#alice @ My VPN-XHTTP" in xhttp
        assert "#alice @ My VPN-WSS" in wss

    def test_no_server_name_omits_at_sign(self) -> None:
        """Without server_name, fragments are just the client name."""
        creds = _make_creds()
        urls = build_protocol_urls("alice", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert reality.endswith("#alice")
        assert " @ " not in reality


class TestBuildProtocolURLsEdgeCases:
    """Additional URL building edge case tests."""

    def test_sni_with_subdomain(self) -> None:
        creds = _make_creds(sni="sub.domain.example.com")
        urls = build_protocol_urls("test", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "sni=sub.domain.example.com" in reality

    def test_all_protocols_together(self) -> None:
        """Test building URLs with all three protocols enabled."""
        creds = _make_creds(domain="example.com", ws_path="myws", xhttp_path="xp")
        urls = build_protocol_urls("alice", "r-uuid", "w-uuid", creds)
        reality = _find_url(urls, "reality")
        xhttp = _find_url(urls, "xhttp")
        wss = _find_url(urls, "wss")
        assert reality.startswith("vless://r-uuid@1.2.3.4:443")
        assert xhttp.startswith("vless://r-uuid@example.com:443")
        assert "path=%2Fxp" in xhttp
        assert "sni=example.com" in xhttp
        assert "fp=chrome" in xhttp
        assert wss.startswith("vless://w-uuid@example.com:443")
        assert "#alice" in reality
        assert "#alice-XHTTP" in xhttp
        assert "#alice-WSS" in wss

    def test_empty_sni_defaults(self) -> None:
        """When SNI is None, should default to www.microsoft.com."""
        creds = ServerCredentials(
            panel=PanelConfig(username="admin", password="pass", port=2053),
            server=ServerConfig(ip="1.2.3.4", sni=None),
            protocols={
                "reality": RealityConfig(
                    uuid="base-uuid",
                    public_key="pk",
                    short_id="sid",
                    private_key="priv",
                ),
            },
        )
        urls = build_protocol_urls("test", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "sni=www.microsoft.com" in reality

    def test_url_contains_encryption_none(self) -> None:
        creds = _make_creds()
        urls = build_protocol_urls("test", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "encryption=none" in reality

    def test_xhttp_no_flow_parameter(self) -> None:
        """XHTTP must NOT include flow parameter (xtls-rprx-vision is incompatible)."""
        creds = _make_creds(xhttp_path="xp")
        urls = build_protocol_urls("test", "uuid-1", "", creds)
        xhttp = _find_url(urls, "xhttp")
        # XHTTP URL should not have flow= at all
        assert "flow=" not in xhttp
        # But reality URL should have it
        reality = _find_url(urls, "reality")
        assert "flow=xtls-rprx-vision" in reality

    def test_wss_url_has_host_header(self) -> None:
        """WSS URL should include host= parameter matching domain."""
        creds = _make_creds(domain="cdn.example.com", ws_path="ws")
        urls = build_protocol_urls("test", "r-uuid", "w-uuid", creds)
        wss = _find_url(urls, "wss")
        assert "host=cdn.example.com" in wss

    def test_xhttp_path_in_url(self) -> None:
        """XHTTP path should be included in the URL."""
        creds = _make_creds(xhttp_path="myxhttppath")
        urls = build_protocol_urls("test", "uuid", "", creds)
        xhttp = _find_url(urls, "xhttp")
        assert "path=%2Fmyxhttppath" in xhttp

    def test_client_name_with_numbers(self) -> None:
        creds = _make_creds()
        urls = build_protocol_urls("user123", "uuid-1", "", creds)
        reality = _find_url(urls, "reality")
        assert "#user123" in reality


class TestQRCodeGeneration:
    """Test QR code generation with segno."""

    def test_generate_qr_terminal_success(self) -> None:
        result = generate_qr_terminal("vless://test@1.2.3.4:443")
        assert result  # non-empty
        assert "\n" in result  # multi-line terminal output

    def test_generate_qr_terminal_empty_input(self) -> None:
        # segno handles empty strings gracefully
        result = generate_qr_terminal("")
        assert isinstance(result, str)

    def test_generate_qr_base64_success(self) -> None:
        result = generate_qr_base64("vless://test@1.2.3.4:443")
        assert result  # non-empty
        # Verify it's valid base64 encoding of a PNG
        import base64

        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"

    def test_generate_qr_base64_empty_input(self) -> None:
        # segno handles empty strings gracefully
        result = generate_qr_base64("")
        assert isinstance(result, str)


class TestSaveConnectionHtml:
    """Test HTML connection page generation."""

    def test_creates_file(self, tmp_path: Path) -> None:
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://uuid@1.2.3.4:443?test")]
        dest = tmp_path / "alice.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4")
        assert dest.exists()

    def test_html_contains_client_name_in_url(self, tmp_path: Path) -> None:
        """Client name appears in the VLESS URL fragment within the HTML."""
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://uuid@1.2.3.4:443?test#bob")]
        dest = tmp_path / "bob.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4", client_name="bob")
        content = dest.read_text()
        assert "vless://uuid@1.2.3.4:443?test#bob" in content

    def test_html_contains_reality_url(self, tmp_path: Path) -> None:
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://unique-reality-url@1.2.3.4:443")]
        dest = tmp_path / "test.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4")
        content = dest.read_text()
        assert "unique-reality-url" in content

    def test_html_file_permissions(self, tmp_path: Path) -> None:
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://x")]
        dest = tmp_path / "test.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4")
        assert oct(dest.stat().st_mode)[-3:] == "600"

    def test_html_is_valid_structure(self, tmp_path: Path) -> None:
        """Generated HTML should have basic structure."""
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://uuid@1.2.3.4:443")]
        dest = tmp_path / "alice.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4")
        content = dest.read_text()
        assert "<!DOCTYPE html>" in content or "<html" in content
        assert "</html>" in content

    def test_html_uses_self_contained_troubleshooting_text(self, tmp_path: Path) -> None:
        """Generated HTML should not depend on an external ping service."""
        urls = [ProtocolURL(key="reality", label="Primary", url="vless://uuid@1.2.3.4:443")]
        dest = tmp_path / "alice.html"
        with patch("meridian.render.generate_qr_base64", return_value=""):
            save_connection_html(urls, dest, "1.2.3.4")
        content = dest.read_text()
        assert "getmeridian.org/ping" not in content
        assert "Check automatic time sync" in content
