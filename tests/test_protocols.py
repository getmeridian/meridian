"""Tests for protocol registry and Protocol abstraction."""

from __future__ import annotations

from meridian.credentials import (
    RealityConfig,
    ServerConfig,
    ServerCredentials,
    WSSConfig,
    XHTTPConfig,
)
from meridian.protocols import (
    PROTOCOL_ORDER,
    PROTOCOLS,
    Hysteria2Protocol,
    Protocol,
    RealityProtocol,
    WSSProtocol,
    XHTTPProtocol,
    get_protocol,
)

# ---------------------------------------------------------------------------
# Protocol ABC and registry
# ---------------------------------------------------------------------------


class TestProtocolRegistry:
    """Tests for the PROTOCOLS dict and get_protocol() function."""

    def test_all_protocols_present(self) -> None:
        keys = list(PROTOCOLS.keys())
        assert keys == ["reality", "xhttp", "wss"]

    def test_protocol_order(self) -> None:
        assert PROTOCOL_ORDER == ["reality", "xhttp", "wss"]

    def test_protocols_are_protocol_instances(self) -> None:
        for p in PROTOCOLS.values():
            assert isinstance(p, Protocol)

    def test_protocol_types(self) -> None:
        assert isinstance(PROTOCOLS["reality"], RealityProtocol)
        assert isinstance(PROTOCOLS["xhttp"], XHTTPProtocol)
        assert isinstance(PROTOCOLS["wss"], WSSProtocol)

    def test_get_protocol_found(self) -> None:
        for key in ("reality", "xhttp", "wss"):
            p = get_protocol(key)
            assert p is not None
            assert p.key == key

    def test_get_protocol_not_found(self) -> None:
        assert get_protocol("nonexistent") is None
        assert get_protocol("") is None

    def test_protocol_keys_unique(self) -> None:
        keys = list(PROTOCOLS.keys())
        assert len(keys) == len(set(keys))

    def test_display_labels(self) -> None:
        """Each protocol should have a human-readable display label."""
        assert PROTOCOLS["reality"].display_label == "Primary"
        assert PROTOCOLS["xhttp"].display_label == "XHTTP"
        assert PROTOCOLS["wss"].display_label == "CDN Backup"


# ---------------------------------------------------------------------------
# Protocol.build_url()
# ---------------------------------------------------------------------------


class TestRealityBuildURL:
    def test_basic_url(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url(
            "test-uuid",
            "alice",
            ip="1.2.3.4",
            sni="www.microsoft.com",
            public_key="myPBK",
            short_id="abc123",
        )
        assert url.startswith("vless://test-uuid@1.2.3.4:443")
        assert "flow=xtls-rprx-vision" in url
        assert "security=reality" in url
        assert "sni=www.microsoft.com" in url
        assert "fp=chrome" in url
        assert "pbk=myPBK" in url
        assert "sid=abc123" in url
        assert "type=tcp" in url
        assert "headerType=none" in url
        assert url.endswith("#alice")

    def test_custom_fingerprint(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url("uuid", "name", ip="1.2.3.4", fingerprint="firefox")
        assert "fp=firefox" in url

    def test_default_sni(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url("uuid", "name", ip="1.2.3.4")
        assert "sni=www.microsoft.com" in url

    def test_different_sni(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url("uuid", "name", ip="5.6.7.8", sni="dl.google.com")
        assert "sni=dl.google.com" in url


class TestXHTTPBuildURL:
    def test_basic_url(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="1.2.3.4",
            xhttp_path="myxhttppath",
        )
        assert url.startswith("vless://test-uuid@1.2.3.4:443")
        assert "security=tls" in url
        assert "type=xhttp" in url
        assert "path=%2Fmyxhttppath" in url
        assert url.endswith("#bob-XHTTP")
        # XHTTP must NOT have flow parameter
        assert "flow=" not in url
        # No Reality params
        assert "pbk=" not in url
        assert "sid=" not in url
        # TLS params (sni defaults to host, fp defaults to chrome)
        assert "sni=1.2.3.4" in url
        assert "fp=chrome" in url

    def test_url_with_domain(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="1.2.3.4",
            xhttp_path="mypath",
            domain="example.com",
        )
        assert url.startswith("vless://test-uuid@example.com:443")
        assert "security=tls" in url
        assert "type=xhttp" in url
        assert "path=%2Fmypath" in url
        assert "sni=example.com" in url
        assert "fp=chrome" in url

    def test_url_without_domain_uses_ip(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="5.6.7.8",
            xhttp_path="p",
        )
        assert "vless://test-uuid@5.6.7.8:443" in url
        assert "sni=5.6.7.8" in url

    def test_url_suffix(self) -> None:
        assert XHTTPProtocol().url_suffix == "-XHTTP"

    def test_shares_uuid_with_reality(self) -> None:
        assert XHTTPProtocol().shares_uuid_with == "reality"

    def test_custom_fingerprint(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="1.2.3.4",
            xhttp_path="p",
            fingerprint="firefox",
        )
        assert "fp=firefox" in url


class TestWSSBuildURL:
    def test_basic_url(self) -> None:
        proto = WSSProtocol()
        url = proto.build_url(
            "wss-uuid",
            "carol",
            domain="example.com",
            ws_path="ws789",
        )
        assert url.startswith("vless://wss-uuid@example.com:443")
        assert "security=tls" in url
        assert "sni=example.com" in url
        assert "type=ws" in url
        assert "host=example.com" in url
        assert "path=%2Fws789" in url
        assert url.endswith("#carol-WSS")

    def test_url_suffix(self) -> None:
        assert WSSProtocol().url_suffix == "-WSS"

    def test_requires_domain(self) -> None:
        assert WSSProtocol().requires_domain is True

    def test_reality_does_not_require_domain(self) -> None:
        assert RealityProtocol().requires_domain is False

    def test_xhttp_does_not_require_domain(self) -> None:
        assert XHTTPProtocol().requires_domain is False

    def test_wss_own_uuid(self) -> None:
        """WSS uses its own UUID (no shares_uuid_with)."""
        assert WSSProtocol().shares_uuid_with is None


# ---------------------------------------------------------------------------
# Credential-aware URL building
# ---------------------------------------------------------------------------


def _make_test_creds(
    ip: str = "198.51.100.1",
    sni: str = "www.microsoft.com",
    domain: str = "",
    xhttp_path: str = "",
    ws_path: str = "",
    encryption_key: str = "",
) -> ServerCredentials:
    """Build test credentials."""
    creds = ServerCredentials(
        server=ServerConfig(ip=ip, sni=sni, domain=domain or None),
        protocols={
            "reality": RealityConfig(
                uuid="r-uuid",
                public_key="testPBK",
                short_id="ab12",
                encryption_key=encryption_key or None,
            ),
        },
    )
    if xhttp_path:
        creds.protocols["xhttp"] = XHTTPConfig(xhttp_path=xhttp_path)
    if ws_path:
        creds.protocols["wss"] = WSSConfig(uuid="w-uuid", ws_path=ws_path)
    return creds


class TestResolveUuid:
    def test_reality_uses_reality_uuid(self) -> None:
        proto = RealityProtocol()
        assert proto._resolve_uuid("r-uuid", "w-uuid") == "r-uuid"

    def test_xhttp_uses_reality_uuid_via_shares(self) -> None:
        proto = XHTTPProtocol()
        assert proto._resolve_uuid("r-uuid", "w-uuid") == "r-uuid"

    def test_wss_uses_wss_uuid(self) -> None:
        proto = WSSProtocol()
        assert proto._resolve_uuid("r-uuid", "w-uuid") == "w-uuid"


class TestBuildUrlFromCreds:
    def test_reality_builds_url(self) -> None:
        creds = _make_test_creds()
        url = RealityProtocol().build_url_from_creds("r-uuid", "", creds, "alice")
        assert url.startswith("vless://r-uuid@198.51.100.1:443")
        assert "sni=www.microsoft.com" in url
        assert "pbk=testPBK" in url
        assert "sid=ab12" in url
        assert url.endswith("#alice")

    def test_reality_with_server_name(self) -> None:
        creds = _make_test_creds()
        url = RealityProtocol().build_url_from_creds("r-uuid", "", creds, "alice", server_name="My VPN")
        assert url.endswith("#alice @ My VPN")

    def test_xhttp_builds_url(self) -> None:
        creds = _make_test_creds(xhttp_path="xp123")
        url = XHTTPProtocol().build_url_from_creds("r-uuid", "", creds, "bob")
        assert "vless://r-uuid@" in url
        assert "type=xhttp" in url
        assert "path=%2Fxp123" in url
        assert url.endswith("#bob-XHTTP")

    def test_xhttp_returns_empty_without_path(self) -> None:
        creds = _make_test_creds()  # no xhttp_path
        url = XHTTPProtocol().build_url_from_creds("r-uuid", "", creds, "bob")
        assert url == ""

    def test_wss_builds_url(self) -> None:
        creds = _make_test_creds(domain="example.com", ws_path="ws789")
        url = WSSProtocol().build_url_from_creds("", "w-uuid", creds, "carol")
        assert "vless://w-uuid@example.com:443" in url
        assert "type=ws" in url
        assert "host=example.com" in url
        assert "path=%2Fws789" in url
        assert url.endswith("#carol-WSS")

    def test_wss_returns_empty_without_domain(self) -> None:
        creds = _make_test_creds(ws_path="ws789")  # no domain
        url = WSSProtocol().build_url_from_creds("", "w-uuid", creds, "carol")
        assert url == ""

    def test_wss_returns_empty_without_uuid(self) -> None:
        creds = _make_test_creds(domain="example.com", ws_path="ws789")
        url = WSSProtocol().build_url_from_creds("", "", creds, "carol")
        assert url == ""

    def test_encryption_key_in_reality(self) -> None:
        creds = _make_test_creds(encryption_key="pq-key-123")
        url = RealityProtocol().build_url_from_creds("r-uuid", "", creds, "alice")
        assert "encryption=pq-key-123" in url


class TestBuildRelayUrl:
    def test_reality_relay_url(self) -> None:
        creds = _make_test_creds()
        url = RealityProtocol().build_relay_url(
            "r-uuid", "", creds, "alice", "198.51.100.50", 8443, relay_name="moscow"
        )
        assert "vless://r-uuid@198.51.100.50:8443" in url
        assert "sni=www.microsoft.com" in url
        assert "#alice-via-moscow" in url

    def test_reality_relay_with_relay_sni(self) -> None:
        creds = _make_test_creds()
        url = RealityProtocol().build_relay_url(
            "r-uuid", "", creds, "alice", "198.51.100.50", relay_sni="relay.example.com"
        )
        assert "sni=relay.example.com" in url

    def test_reality_relay_with_server_name(self) -> None:
        creds = _make_test_creds()
        url = RealityProtocol().build_relay_url(
            "r-uuid", "", creds, "alice", "198.51.100.50", relay_name="moscow", server_name="My VPN"
        )
        assert "#alice @ My VPN-via-moscow" in url

    def test_xhttp_relay_url(self) -> None:
        creds = _make_test_creds(xhttp_path="xp123")
        url = XHTTPProtocol().build_relay_url("r-uuid", "", creds, "bob", "198.51.100.50", 8443, relay_name="moscow")
        assert "vless://r-uuid@198.51.100.50:8443" in url
        assert "sni=198.51.100.1" in url  # exit IP as SNI (no domain)
        assert "type=xhttp" in url
        assert "#bob-via-moscow-XHTTP" in url

    def test_xhttp_relay_with_domain(self) -> None:
        creds = _make_test_creds(domain="example.com", xhttp_path="xp123")
        url = XHTTPProtocol().build_relay_url("r-uuid", "", creds, "bob", "198.51.100.50", relay_name="moscow")
        assert "sni=example.com" in url

    def test_xhttp_relay_returns_empty_without_path(self) -> None:
        creds = _make_test_creds()
        url = XHTTPProtocol().build_relay_url("r-uuid", "", creds, "bob", "198.51.100.50")
        assert url == ""

    def test_xhttp_relay_with_relay_sni(self) -> None:
        creds = _make_test_creds(domain="example.com", xhttp_path="xp123")
        url = XHTTPProtocol().build_relay_url(
            "r-uuid", "", creds, "bob", "198.51.100.50", relay_sni="yandex.ru", relay_name="moscow"
        )
        assert "sni=yandex.ru" in url
        assert "sni=example.com" not in url

    def test_wss_relay_url(self) -> None:
        creds = _make_test_creds(domain="example.com", ws_path="ws789")
        url = WSSProtocol().build_relay_url("", "w-uuid", creds, "carol", "198.51.100.50", 8443, relay_name="moscow")
        assert "vless://w-uuid@198.51.100.50:8443" in url
        assert "sni=example.com" in url
        assert "host=example.com" in url
        assert "#carol-via-moscow-WSS" in url

    def test_wss_relay_with_relay_sni(self) -> None:
        creds = _make_test_creds(domain="example.com", ws_path="ws789")
        url = WSSProtocol().build_relay_url(
            "", "w-uuid", creds, "carol", "198.51.100.50", relay_sni="yandex.ru", relay_name="moscow"
        )
        assert "sni=yandex.ru" in url
        assert "sni=example.com" not in url
        assert "host=example.com" in url  # host stays as exit domain for routing

    def test_wss_relay_returns_empty_without_domain(self) -> None:
        creds = _make_test_creds(ws_path="ws789")
        url = WSSProtocol().build_relay_url("", "w-uuid", creds, "carol", "198.51.100.50")
        assert url == ""


# ---------------------------------------------------------------------------
# IPv6 URL construction
# ---------------------------------------------------------------------------


class TestIPv6URLConstruction:
    """Verify that IPv6 addresses are properly bracketed in VLESS URLs."""

    def test_reality_ipv6_brackets(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url(
            "test-uuid",
            "alice",
            ip="2001:db8::1",
            sni="www.microsoft.com",
            public_key="myPBK",
            short_id="abc123",
        )
        assert "vless://test-uuid@[2001:db8::1]:443" in url
        assert "security=reality" in url
        assert "sni=www.microsoft.com" in url

    def test_xhttp_ipv6_brackets_no_domain(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="2001:db8::1",
            xhttp_path="mypath",
        )
        assert "vless://test-uuid@[2001:db8::1]:443" in url
        assert "sni=[2001:db8::1]" in url

    def test_xhttp_ipv6_with_domain_uses_domain(self) -> None:
        proto = XHTTPProtocol()
        url = proto.build_url(
            "test-uuid",
            "bob",
            ip="2001:db8::1",
            xhttp_path="mypath",
            domain="example.com",
        )
        assert "vless://test-uuid@example.com:443" in url
        assert "sni=example.com" in url
        # IPv6 should NOT appear when domain is set
        assert "[2001:db8::1]" not in url

    def test_reality_ipv4_no_brackets(self) -> None:
        proto = RealityProtocol()
        url = proto.build_url(
            "test-uuid",
            "alice",
            ip="198.51.100.1",
        )
        assert "vless://test-uuid@198.51.100.1:443" in url
        assert "[198.51.100.1]" not in url


# ---------------------------------------------------------------------------
# Hysteria2 protocol
# ---------------------------------------------------------------------------


class TestHysteria2Protocol:
    def test_basic_url(self) -> None:
        proto = Hysteria2Protocol()
        url = proto.build_url("test-uuid", "alice", ip="198.51.100.1", port=443, sni="")
        assert url.startswith("hysteria2://test-uuid@198.51.100.1:443")
        assert "insecure=1" in url

    def test_url_with_sni_no_insecure(self) -> None:
        proto = Hysteria2Protocol()
        url = proto.build_url("test-uuid", "alice", ip="198.51.100.1", port=443, sni="vpn.example.com")
        assert "sni=vpn.example.com" in url
        assert "insecure=1" not in url

    def test_url_without_sni_uses_ip(self) -> None:
        proto = Hysteria2Protocol()
        url = proto.build_url("test-uuid", "alice", ip="198.51.100.1", port=443)
        assert "sni=198.51.100.1" in url

    def test_get_protocol_finds_hysteria2(self) -> None:
        proto = get_protocol("hysteria2")
        assert proto is not None
        assert proto.key == "hysteria2"

    def test_display_label(self) -> None:
        assert Hysteria2Protocol().display_label == "UDP (Experimental)"

    def test_url_suffix(self) -> None:
        assert Hysteria2Protocol().url_suffix == "-HY2"
