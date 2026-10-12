"""Tests for config module — IP validation, path sanitization, and env overrides."""

from __future__ import annotations

import importlib

import meridian.config as config
from meridian.config import (
    CONNECT_TEST_URL_IPV4,
    CONNECT_TEST_URL_IPV6,
    is_ip,
    is_ipv4,
    sanitize_ip_for_path,
)


class TestIsIpv4:
    def test_valid_ipv4(self) -> None:
        assert is_ipv4("198.51.100.1") is True

    def test_rejects_ipv6(self) -> None:
        assert is_ipv4("2001:db8::1") is False

    def test_rejects_hostname(self) -> None:
        assert is_ipv4("example.com") is False


class TestIsIp:
    def test_accepts_ipv4(self) -> None:
        assert is_ip("198.51.100.1") is True

    def test_accepts_ipv6_compressed(self) -> None:
        assert is_ip("2001:db8::1") is True

    def test_accepts_ipv6_full(self) -> None:
        assert is_ip("2001:0db8:0000:0000:0000:0000:0000:0001") is True

    def test_accepts_ipv6_loopback(self) -> None:
        assert is_ip("::1") is True

    def test_rejects_hostname(self) -> None:
        assert is_ip("example.com") is False

    def test_rejects_empty_string(self) -> None:
        assert is_ip("") is False

    def test_rejects_brackets(self) -> None:
        # Brackets are URL notation, not part of the address
        assert is_ip("[2001:db8::1]") is False

    def test_rejects_garbage(self) -> None:
        assert is_ip("not-an-ip") is False

    def test_rejects_ipv4_overflow(self) -> None:
        assert is_ip("256.1.2.3") is False


class TestSanitizeIpForPath:
    def test_ipv4_unchanged(self) -> None:
        assert sanitize_ip_for_path("198.51.100.1") == "198.51.100.1"

    def test_ipv6_colons_replaced(self) -> None:
        assert sanitize_ip_for_path("2001:db8::1") == "2001-db8--1"

    def test_ipv6_full_form(self) -> None:
        result = sanitize_ip_for_path("2001:0db8:0000:0000:0000:0000:0000:0001")
        assert result == "2001-0db8-0000-0000-0000-0000-0000-0001"

    def test_ipv6_loopback(self) -> None:
        assert sanitize_ip_for_path("::1") == "--1"


class TestEnvOverrides:
    def test_connect_test_url_defaults_to_address_family_endpoints(self) -> None:
        assert config.CONNECT_TEST_URL == ""
        assert CONNECT_TEST_URL_IPV4 == "https://api.ipify.org"
        assert CONNECT_TEST_URL_IPV6 == "https://api6.ipify.org"

    def test_acme_server_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_ACME_SERVER", "https://pebble.test/dir")
        importlib.reload(config)
        assert config.ACME_SERVER == "https://pebble.test/dir"

    def test_connect_test_url_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_CONNECT_TEST_URL", "https://echo.test/ip")
        importlib.reload(config)
        assert config.CONNECT_TEST_URL == "https://echo.test/ip"
