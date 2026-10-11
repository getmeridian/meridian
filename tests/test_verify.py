"""Tests for xray client utility functions."""

from __future__ import annotations

from meridian.xray_client import (
    _parse_dgst,
    _resolve_asset_name,
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
