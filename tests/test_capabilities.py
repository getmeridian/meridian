"""Tests for protocol capability detection."""

from __future__ import annotations

from types import SimpleNamespace

from meridian.capabilities import (
    NodeCapabilities,
    _parse_xray_version,
    detect_capabilities,
)

# ---------------------------------------------------------------------------
# from_xray_version
# ---------------------------------------------------------------------------


class TestFromXrayVersion:
    """NodeCapabilities.from_xray_version with various version strings."""

    def test_empty_version_gets_safe_defaults(self) -> None:
        caps = NodeCapabilities.from_xray_version("")
        assert caps.xray_version == ""
        assert caps.reality is True
        assert caps.xhttp is True
        assert caps.wss is True
        assert caps.hysteria2 is False
        assert caps.finalmask is False
        assert caps.xhttp_h2 is False
        assert caps.xhttp_h3 is False

    def test_version_25_no_advanced_features(self) -> None:
        caps = NodeCapabilities.from_xray_version("25.1.0")
        assert caps.xray_version == "25.1.0"
        assert caps.reality is True
        assert caps.xhttp is True
        assert caps.wss is True
        assert caps.hysteria2 is False
        assert caps.finalmask is False

    def test_version_26_enables_hysteria2_and_finalmask(self) -> None:
        caps = NodeCapabilities.from_xray_version("26.3.27")
        assert caps.xray_version == "26.3.27"
        assert caps.reality is True
        assert caps.xhttp is True
        assert caps.wss is True
        assert caps.hysteria2 is True
        assert caps.finalmask is True

    def test_version_27_also_enables_advanced(self) -> None:
        caps = NodeCapabilities.from_xray_version("27.0.0")
        assert caps.hysteria2 is True
        assert caps.finalmask is True

    def test_version_1_legacy(self) -> None:
        """Legacy Xray v1.x — only base capabilities."""
        caps = NodeCapabilities.from_xray_version("1.8.24")
        assert caps.xray_version == "1.8.24"
        assert caps.reality is True
        assert caps.hysteria2 is False
        assert caps.finalmask is False

    def test_garbage_version_string(self) -> None:
        """Non-numeric version string — safe defaults, no crash."""
        caps = NodeCapabilities.from_xray_version("unknown")
        assert caps.xray_version == "unknown"
        assert caps.reality is True
        assert caps.hysteria2 is False

    def test_partial_version_no_dots(self) -> None:
        """Single number, no dots — should still parse major."""
        caps = NodeCapabilities.from_xray_version("26")
        assert caps.hysteria2 is True


# ---------------------------------------------------------------------------
# _parse_xray_version
# ---------------------------------------------------------------------------


class TestParseXrayVersion:
    """Extract semver from various Xray output formats."""

    def test_full_xray_banner(self) -> None:
        output = "Xray 26.3.27 (Xray, Penetrates Everything.)"
        assert _parse_xray_version(output) == "26.3.27"

    def test_bare_version(self) -> None:
        assert _parse_xray_version("26.3.27") == "26.3.27"

    def test_v_prefixed(self) -> None:
        assert _parse_xray_version("v1.8.24") == "1.8.24"

    def test_multiline_output(self) -> None:
        output = "Xray 26.3.27 (Xray, Penetrates Everything.)\nSome other line"
        assert _parse_xray_version(output) == "26.3.27"

    def test_no_version_found(self) -> None:
        assert _parse_xray_version("no version here") == ""

    def test_empty_string(self) -> None:
        assert _parse_xray_version("") == ""

    def test_version_embedded_in_noise(self) -> None:
        output = "build at 2025-01-15, version 26.2.6-beta with go1.22"
        assert _parse_xray_version(output) == "26.2.6"


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    """Serialization to dict for JSON output."""

    def test_includes_all_fields(self) -> None:
        caps = NodeCapabilities.from_xray_version("26.3.27")
        d = caps.to_dict()
        assert d["xray_version"] == "26.3.27"
        assert d["reality"] is True
        assert d["hysteria2"] is True
        assert d["finalmask"] is True
        assert d["xhttp_h3"] is False

    def test_empty_version_dict(self) -> None:
        caps = NodeCapabilities()
        d = caps.to_dict()
        assert d["xray_version"] == ""
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# detect_capabilities (with mock connection)
# ---------------------------------------------------------------------------


class TestDetectCapabilities:
    """detect_capabilities with a fake ServerConnection."""

    def test_detects_version_from_docker_exec(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 30) -> object:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Xray 26.3.27 (Xray, Penetrates Everything.)\n",
                )

        caps = detect_capabilities(FakeConn())
        assert caps.xray_version == "26.3.27"
        assert caps.hysteria2 is True
        assert caps.finalmask is True

    def test_container_not_running_returns_empty_version(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 30) -> object:
                return SimpleNamespace(returncode=1, stdout="")

        caps = detect_capabilities(FakeConn())
        assert caps.xray_version == ""
        assert caps.reality is True
        assert caps.hysteria2 is False

    def test_unparseable_output_returns_empty_version(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 30) -> object:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Error: something went wrong\n",
                )

        caps = detect_capabilities(FakeConn())
        assert caps.xray_version == ""
        assert caps.reality is True

    def test_legacy_xray_version(self) -> None:
        class FakeConn:
            def run(self, cmd: str, timeout: int = 30) -> object:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Xray 1.8.24 (Xray, Penetrates Everything.)\n",
                )

        caps = detect_capabilities(FakeConn())
        assert caps.xray_version == "1.8.24"
        assert caps.hysteria2 is False
        assert caps.finalmask is False
        assert caps.reality is True
