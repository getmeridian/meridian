"""Tests for config module — pinned images and environment overrides."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version

import pytest

import meridian.config as config


class TestDependencies:
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
