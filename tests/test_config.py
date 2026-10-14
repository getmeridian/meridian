"""Tests for config module — pinned images and environment overrides."""

from __future__ import annotations

import importlib
import re
from importlib.metadata import PackageNotFoundError, version

import pytest

import meridian.config as config


class TestPinnedImages:
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
