"""Tests for protocol/inbound type registry and Protocol abstraction."""

from __future__ import annotations

from meridian.credentials import (
    RealityConfig,
    ServerConfig,
    ServerCredentials,
    WSSConfig,
    XHTTPConfig,
)
from meridian.models import Inbound
from meridian.protocols import (
    INBOUND_TYPES,
    PROTOCOL_ORDER,
    PROTOCOLS,
    InboundType,
    Protocol,
    RealityProtocol,
    WSSProtocol,
    XHTTPProtocol,
    available_protocols,
    get_protocol,
)


class TestInboundTypes:
    def test_all_types_present(self) -> None:
        assert set(INBOUND_TYPES.keys()) == {"reality", "wss", "xhttp"}

    def test_type_is_frozen_dataclass(self) -> None:
        for t in INBOUND_TYPES.values():
            assert isinstance(t, InboundType)

    def test_reality_values(self) -> None:
        r = INBOUND_TYPES["reality"]
        assert r.remark == "VLESS-Reality"
        assert r.email_prefix == "reality-"
        assert r.flow == "xtls-rprx-vision"

    def test_wss_values(self) -> None:
        w = INBOUND_TYPES["wss"]
        assert w.remark == "VLESS-WSS"
        assert w.email_prefix == "wss-"
        assert w.flow == ""

    def test_xhttp_values(self) -> None:
        x = INBOUND_TYPES["xhttp"]
        assert x.remark == "VLESS-Reality-XHTTP"
        assert x.email_prefix == "xhttp-"
        assert x.flow == ""

    def test_email_prefixes_unique(self) -> None:
        prefixes = [t.email_prefix for t in INBOUND_TYPES.values()]
        assert len(prefixes) == len(set(prefixes))

    def test_remarks_unique(self) -> None:
        remarks = [t.remark for t in INBOUND_TYPES.values()]
        assert len(remarks) == len(set(remarks))


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

    def test_protocol_references_inbound_types(self) -> None:
        """Each protocol's inbound_type must be the same object from INBOUND_TYPES."""
        for p in PROTOCOLS.values():
            assert p.inbound_type is INBOUND_TYPES[p.key]

    def test_remark_and_email_prefix_convenience(self) -> None:
        """Convenience properties must match the inbound_type."""
        for p in PROTOCOLS.values():
            assert p.remark == p.inbound_type.remark
            assert p.email_prefix == p.inbound_type.email_prefix

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
# Protocol.client_settings()
# ---------------------------------------------------------------------------


class TestClientSettings:
    def test_reality_client_settings(self) -> None:
        proto = RealityProtocol()
        settings = proto.client_settings("my-uuid", "reality-alice")
        clients = settings["clients"]
        assert len(clients) == 1
        c = clients[0]
        assert c["id"] == "my-uuid"
        assert c["flow"] == "xtls-rprx-vision"
        assert c["email"] == "reality-alice"
        assert c["enable"] is True
        assert c["limitIp"] == 2

    def test_xhttp_client_settings(self) -> None:
        proto = XHTTPProtocol()
        settings = proto.client_settings("my-uuid", "xhttp-alice")
        c = settings["clients"][0]
        assert c["id"] == "my-uuid"
        assert c["flow"] == ""  # XHTTP has no flow
        assert c["email"] == "xhttp-alice"

    def test_wss_client_settings(self) -> None:
        proto = WSSProtocol()
        settings = proto.client_settings("wss-uuid", "wss-alice")
        c = settings["clients"][0]
        assert c["id"] == "wss-uuid"
        assert c["flow"] == ""  # WSS has no flow
        assert c["email"] == "wss-alice"

    def test_all_settings_have_required_fields(self) -> None:
        """All protocols must produce settings with the required 3x-ui fields."""
        required_keys = {"id", "flow", "email", "limitIp", "totalGB", "expiryTime", "enable"}
        for proto in PROTOCOLS.values():
            settings = proto.client_settings("uuid", "email")
            c = settings["clients"][0]
            assert required_keys.issubset(c.keys()), f"{proto.key} missing fields"

    def test_base_class_method_used(self) -> None:
        """client_settings() is defined on the base Protocol class, not duplicated."""
        # All three should use the same method object from Protocol
        for proto in PROTOCOLS.values():
            assert proto.client_settings.__func__ is Protocol.client_settings  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Protocol.find_inbound()
# ---------------------------------------------------------------------------


def _make_inbound(remark: str, port: int = 443) -> Inbound:
    return Inbound(id=1, remark=remark, protocol="vless", port=port)


class TestFindInbound:
    def test_finds_matching_inbound(self) -> None:
        inbounds = [
            _make_inbound("VLESS-Reality"),
            _make_inbound("VLESS-WSS"),
        ]
        proto = RealityProtocol()
        ib = proto.find_inbound(inbounds)
        assert ib is not None
        assert ib.remark == "VLESS-Reality"

    def test_returns_none_when_missing(self) -> None:
        inbounds = [_make_inbound("VLESS-WSS")]
        proto = RealityProtocol()
        assert proto.find_inbound(inbounds) is None

    def test_returns_none_for_empty_list(self) -> None:
        for proto in PROTOCOLS.values():
            assert proto.find_inbound([]) is None


# ---------------------------------------------------------------------------
# available_protocols()
# ---------------------------------------------------------------------------


class TestAvailableProtocols:
    def test_all_available(self) -> None:
        inbounds = [
            _make_inbound("VLESS-Reality"),
            _make_inbound("VLESS-Reality-XHTTP", port=12345),
            _make_inbound("VLESS-WSS"),
        ]
        result = available_protocols(inbounds, domain="example.com")
        keys = [p.key for p in result]
        assert keys == ["reality", "xhttp", "wss"]

    def test_reality_only(self) -> None:
        inbounds = [_make_inbound("VLESS-Reality")]
        result = available_protocols(inbounds)
        assert len(result) == 1
        assert result[0].key == "reality"

    def test_wss_excluded_without_domain(self) -> None:
        inbounds = [
            _make_inbound("VLESS-Reality"),
            _make_inbound("VLESS-WSS"),
        ]
        result = available_protocols(inbounds, domain="")
        keys = [p.key for p in result]
        assert "wss" not in keys
        assert "reality" in keys

    def test_wss_included_with_domain(self) -> None:
        inbounds = [
            _make_inbound("VLESS-Reality"),
            _make_inbound("VLESS-WSS"),
        ]
        result = available_protocols(inbounds, domain="example.com")
        keys = [p.key for p in result]
        assert "wss" in keys

    def test_empty_inbounds(self) -> None:
        result = available_protocols([])
        assert result == []

    def test_unknown_inbound_ignored(self) -> None:
        inbounds = [
            _make_inbound("VLESS-Reality"),
            _make_inbound("Something-Else"),
        ]
        result = available_protocols(inbounds)
        assert len(result) == 1
        assert result[0].key == "reality"

    def test_preserves_protocol_order(self) -> None:
        """Even if inbounds are in different order, result follows PROTOCOLS order."""
        inbounds = [
            _make_inbound("VLESS-WSS"),
            _make_inbound("VLESS-Reality-XHTTP", port=9999),
            _make_inbound("VLESS-Reality"),
        ]
        result = available_protocols(inbounds, domain="example.com")
        keys = [p.key for p in result]
        assert keys == ["reality", "xhttp", "wss"]


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

    def test_pq_encryption_in_reality(self) -> None:
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

    def test_xhttp_relay_ignores_relay_sni(self) -> None:
        # XHTTP TLS is terminated by nginx on the exit using its server_name
        # cert (domain or exit IP). The relay's Reality SNI must NOT be used
        # — it would route XHTTP traffic to the per-relay Reality inbound,
        # whose dest fallback would return 404 for the XHTTP path.
        creds = _make_test_creds(domain="example.com", xhttp_path="xp123")
        url = XHTTPProtocol().build_relay_url(
            "r-uuid", "", creds, "bob", "198.51.100.50", relay_sni="yandex.ru", relay_name="moscow"
        )
        assert "sni=example.com" in url
        assert "sni=yandex.ru" not in url

    def test_xhttp_relay_ip_mode_ignores_relay_sni(self) -> None:
        creds = _make_test_creds(xhttp_path="xp123")
        url = XHTTPProtocol().build_relay_url(
            "r-uuid", "", creds, "bob", "198.51.100.50", relay_sni="yandex.ru", relay_name="moscow"
        )
        assert "sni=198.51.100.1" in url  # exit IP, not relay's Reality SNI
        assert "sni=yandex.ru" not in url

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
