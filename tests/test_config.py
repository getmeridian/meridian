"""Tests for config module — IP validation and environment overrides."""

from __future__ import annotations

import importlib
import re
from importlib.metadata import PackageNotFoundError, version

import pytest

import meridian.config as config
from meridian.core.inputs import is_ip


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


class TestPinnedRemnawaveTuple:
    def test_runtime_components_match_tested_3_4_tuple(self) -> None:
        assert config.REMNAWAVE_BACKEND_IMAGE.startswith("remnawave/backend:3.4.5@sha256:")
        assert config.REMNAWAVE_NODE_IMAGE.startswith("remnawave/node:3.4.2@sha256:")
        assert config.REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE.startswith("remnawave/subscription-page:8.0.0@sha256:")
        assert config.REMNAWAVE_MIN_VERSION == "3.4.0"
        assert config.XRAY_VERSION == "26.7.28"

    @pytest.mark.parametrize(
        "name",
        [
            "REMNAWAVE_BACKEND_IMAGE",
            "REMNAWAVE_NODE_IMAGE",
            "REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE",
            "REMNAWAVE_POSTGRES_IMAGE",
            "REMNAWAVE_VALKEY_IMAGE",
        ],
    )
    def test_every_installed_image_pins_an_exact_tag_and_index_digest(self, name: str) -> None:
        # A mutable tag alone lets a re-push change what fresh servers run.
        image = getattr(config, name)
        assert re.fullmatch(r"[a-z0-9./-]+:[0-9][0-9A-Za-z.-]*@sha256:[0-9a-f]{64}", image), image

    def test_remnawave_sdk_is_not_a_dependency(self) -> None:
        with pytest.raises(PackageNotFoundError):
            version("remnawave")


class TestEnvOverrides:
    def test_acme_server_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_ACME_SERVER", "https://pebble.test/dir")
        importlib.reload(config)
        assert config.ACME_SERVER == "https://pebble.test/dir"

    def test_connect_test_url_override(self, monkeypatch) -> None:
        monkeypatch.setenv("MERIDIAN_CONNECT_TEST_URL", "https://echo.test/ip")
        importlib.reload(config)
        assert config.CONNECT_TEST_URLS_IPV4 == config.CONNECT_TEST_URLS_IPV6 == ("https://echo.test/ip",)
