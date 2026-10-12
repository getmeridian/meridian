"""Tests for xray client config generation and utility functions."""

from __future__ import annotations

import json

from meridian.xray_client import (
    _parse_dgst,
    _resolve_asset_name,
    build_reality_config,
    build_test_configs,
    build_wss_config,
    build_xhttp_config,
)


class TestAssetNameResolution:
    def test_known_platforms(self) -> None:
        from unittest.mock import patch

        cases = [
            ("Darwin", "arm64", "Xray-macos-arm64-v8a.zip"),
            ("Darwin", "x86_64", "Xray-macos-64.zip"),
            ("Linux", "x86_64", "Xray-linux-64.zip"),
            ("Linux", "aarch64", "Xray-linux-arm64-v8a.zip"),
        ]
        for system, machine, expected in cases:
            with patch("meridian.xray_client.platform") as mock_platform:
                mock_platform.system.return_value = system
                mock_platform.machine.return_value = machine
                assert _resolve_asset_name() == expected, f"Failed for {system}/{machine}"

    def test_unknown_platform_returns_none(self) -> None:
        from unittest.mock import patch

        with patch("meridian.xray_client.platform") as mock_platform:
            mock_platform.system.return_value = "Windows"
            mock_platform.machine.return_value = "AMD64"
            assert _resolve_asset_name() is None


class TestDgstParsing:
    def test_parse_sha256(self) -> None:
        content = (
            "MD5= c7253cf3e605d261f5e1a4a55f447d9d\n"
            "SHA1= f02425f9dc1e353388dc9042914b7a0a809b0272\n"
            "SHA2-256= 2e93a67e8aa1936ecefb307e120830fcbd4c643ab9b1c46a2d0838d5f8409eaf\n"
            "SHA2-512= 55683e386cbc5028001a65ee666660ff\n"
        )
        assert _parse_dgst(content) == "2e93a67e8aa1936ecefb307e120830fcbd4c643ab9b1c46a2d0838d5f8409eaf"

    def test_parse_empty(self) -> None:
        assert _parse_dgst("") == ""

    def test_parse_no_sha256(self) -> None:
        assert _parse_dgst("MD5= abc123\nSHA1= def456\n") == ""


class TestRealityConfig:
    def test_structure(self) -> None:
        config = build_reality_config(
            socks_port=10808,
            server_ip="198.51.100.1",
            uuid="test-uuid",
            sni="www.cloudflare.com",
            public_key="testpbk",
            short_id="abcd1234",
        )
        assert config["log"]["loglevel"] == "none"
        assert config["inbounds"][0]["port"] == 10808
        assert config["inbounds"][0]["protocol"] == "socks"
        assert config["inbounds"][0]["listen"] == "127.0.0.1"

        outbound = config["outbounds"][0]
        assert outbound["protocol"] == "vless"
        vnext = outbound["settings"]["vnext"][0]
        assert vnext["address"] == "198.51.100.1"
        assert vnext["port"] == 443
        assert vnext["users"][0]["id"] == "test-uuid"
        assert vnext["users"][0]["flow"] == "xtls-rprx-vision"
        assert vnext["users"][0]["encryption"] == "none"

        stream = outbound["streamSettings"]
        assert stream["network"] == "tcp"
        assert stream["security"] == "reality"
        assert stream["realitySettings"]["publicKey"] == "testpbk"
        assert stream["realitySettings"]["shortId"] == "abcd1234"
        assert stream["realitySettings"]["serverName"] == "www.cloudflare.com"

    def test_pq_encryption(self) -> None:
        config = build_reality_config(
            socks_port=10808,
            server_ip="198.51.100.1",
            uuid="test-uuid",
            sni="www.cloudflare.com",
            public_key="testpbk",
            short_id="abcd1234",
            encryption="mlkem768x25519plus.native.0rtt.testkey",
        )
        user = config["outbounds"][0]["settings"]["vnext"][0]["users"][0]
        assert user["encryption"] == "mlkem768x25519plus.native.0rtt.testkey"

    def test_is_valid_json(self) -> None:
        config = build_reality_config(10808, "198.51.100.1", "uuid", "sni", "pbk", "sid")
        # Roundtrip through JSON
        assert json.loads(json.dumps(config)) == config


class TestXHTTPConfig:
    def test_structure(self) -> None:
        config = build_xhttp_config(
            socks_port=10809,
            host="198.51.100.1",
            uuid="test-uuid",
            xhttp_path="myxhttp",
        )
        outbound = config["outbounds"][0]
        stream = outbound["streamSettings"]
        assert stream["network"] == "xhttp"
        assert stream["security"] == "tls"
        assert stream["tlsSettings"]["serverName"] == "198.51.100.1"
        assert stream["xhttpSettings"]["path"] == "/myxhttp"
        # XHTTP must NOT have flow
        assert "flow" not in outbound["settings"]["vnext"][0]["users"][0]


class TestWSSConfig:
    def test_structure(self) -> None:
        config = build_wss_config(
            socks_port=10810,
            domain="example.com",
            uuid="wss-uuid",
            ws_path="myws",
        )
        outbound = config["outbounds"][0]
        stream = outbound["streamSettings"]
        assert stream["network"] == "ws"
        assert stream["security"] == "tls"
        assert stream["tlsSettings"]["serverName"] == "example.com"
        assert stream["wsSettings"]["path"] == "/myws"
        assert stream["wsSettings"]["headers"]["Host"] == "example.com"
        # WSS must NOT have flow
        assert "flow" not in outbound["settings"]["vnext"][0]["users"][0]


class TestBuildTestConfigs:
    def test_reality_only(self) -> None:
        from meridian.credentials import PanelConfig, RealityConfig, ServerConfig, ServerCredentials

        creds = ServerCredentials(
            panel=PanelConfig(username="admin", password="pass", port=2053),
            server=ServerConfig(ip="198.51.100.1", sni="www.cloudflare.com"),
            protocols={
                "reality": RealityConfig(uuid="uuid-1", public_key="pbk", short_id="sid", private_key="priv"),
            },
        )
        configs = build_test_configs(creds)
        assert len(configs) == 1
        assert configs[0][0] == "Reality (TCP)"
        assert configs[0][2] is True  # expect_ip_match

    def test_all_protocols(self) -> None:
        from meridian.credentials import (
            PanelConfig,
            RealityConfig,
            ServerConfig,
            ServerCredentials,
            WSSConfig,
            XHTTPConfig,
        )

        creds = ServerCredentials(
            panel=PanelConfig(username="admin", password="pass", port=2053),
            server=ServerConfig(ip="198.51.100.1", sni="www.cloudflare.com", domain="example.com"),
            protocols={
                "reality": RealityConfig(uuid="uuid-1", public_key="pbk", short_id="sid", private_key="priv"),
                "xhttp": XHTTPConfig(xhttp_path="xp"),
                "wss": WSSConfig(uuid="wss-uuid", ws_path="ws"),
            },
        )
        configs = build_test_configs(creds)
        labels = [c[0] for c in configs]
        assert "Reality (TCP)" in labels
        assert "XHTTP" in labels
        assert "WSS (CDN)" in labels

    def test_no_creds_returns_empty(self) -> None:
        from meridian.credentials import PanelConfig, ServerConfig, ServerCredentials

        creds = ServerCredentials(
            panel=PanelConfig(username="admin", password="pass", port=2053),
            server=ServerConfig(ip="198.51.100.1"),
            protocols={},
        )
        configs = build_test_configs(creds)
        assert configs == []

    def test_warp_disables_ip_match(self) -> None:
        """WARP routes through Cloudflare — exit IP won't match server IP."""
        from meridian.credentials import PanelConfig, RealityConfig, ServerConfig, ServerCredentials, XHTTPConfig

        creds = ServerCredentials(
            panel=PanelConfig(username="admin", password="pass", port=2053),
            server=ServerConfig(ip="198.51.100.1", sni="www.cloudflare.com", warp=True),
            protocols={
                "reality": RealityConfig(uuid="uuid-1", public_key="pbk", short_id="sid", private_key="priv"),
                "xhttp": XHTTPConfig(xhttp_path="xp"),
            },
        )
        configs = build_test_configs(creds)
        # With WARP, no protocol should expect IP match
        for label, _config, expect_ip_match in configs:
            assert expect_ip_match is False, f"{label} should not expect IP match with WARP"
