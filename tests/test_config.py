"""Tests for config module — IP validation and environment overrides."""

from __future__ import annotations

import importlib

import meridian.config as config
from meridian.config import is_ip


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


class TestEnvOverrides:
    def test_acme_server_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_ACME_SERVER", "https://pebble.test/dir")
        importlib.reload(config)
        assert config.ACME_SERVER == "https://pebble.test/dir"

    def test_connect_test_url_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_CONNECT_TEST_URL", "https://echo.test/ip")
        importlib.reload(config)
        assert config.CONNECT_TEST_URL == "https://echo.test/ip"
