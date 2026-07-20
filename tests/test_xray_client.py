"""Tests for xray client config generation and helpers."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, NodeEntry
from meridian.config import XRAY_VERSION
from meridian.health import ReadinessTimeout
from meridian.xray_client import (
    XrayStartupError,
    _find_free_port,
    _parse_dgst,
    build_reality_config,
    build_test_configs_from_cluster,
    build_wss_config,
    build_xhttp_config,
    ensure_xray_binary,
)
from meridian.xray_client import (
    test_connection as run_connection_test,
)

# ---------------------------------------------------------------------------
# _parse_dgst
# ---------------------------------------------------------------------------


class TestParseDgst:
    def test_valid_dgst_extracts_hash(self) -> None:
        content = "SHA2-256=abc123def456"
        assert _parse_dgst(content) == "abc123def456"

    def test_empty_content_returns_empty(self) -> None:
        assert _parse_dgst("") == ""

    def test_malformed_content_returns_empty(self) -> None:
        assert _parse_dgst("not a dgst file") == ""
        assert _parse_dgst("MD5=abc123") == ""
        assert _parse_dgst("SHA2-256") == ""

    def test_multiple_lines_extracts_correct_hash(self) -> None:
        content = "MD5=somethingelse\nSHA1=anotherhash\nSHA2-256=correcthash789\nSHA2-512=longhash\n"
        assert _parse_dgst(content) == "correcthash789"

    def test_hash_with_whitespace_is_stripped(self) -> None:
        content = "SHA2-256=  abc123  \n"
        assert _parse_dgst(content) == "abc123"


def test_xray_download_extracts_the_runtime_asset_set(tmp_path: Path) -> None:
    bin_path = tmp_path / "xray-test"
    bin_path.write_bytes(b"stale executable")
    bin_path.chmod(0o755)

    def curl_result(args: list[str], **_kwargs: object) -> SimpleNamespace:
        if "-o" not in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        archive_path = Path(args[args.index("-o") + 1])
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Xray", b"current executable")
            archive.writestr("geoip.dat", b"geoip")
            archive.writestr("geosite.dat", b"geosite")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("meridian.xray_client._xray_bin_path", return_value=bin_path),
        patch("meridian.xray_client._resolve_asset_name", return_value="Xray-linux-64.zip"),
        patch("meridian.xray_client.subprocess.run", side_effect=curl_result),
    ):
        assert ensure_xray_binary() == bin_path

    assert bin_path.read_bytes() == b"current executable"
    assert (tmp_path / "geoip.dat").read_bytes() == b"geoip"
    assert (tmp_path / "geosite.dat").read_bytes() == b"geosite"
    assert (tmp_path / ".xray-assets-version").read_text(encoding="utf-8").strip() == XRAY_VERSION


def test_xray_cache_does_not_publish_a_partially_replaced_asset_set(tmp_path: Path) -> None:
    bin_path = tmp_path / "xray-test"
    bin_path.write_bytes(b"old executable")
    bin_path.chmod(0o755)
    (tmp_path / "geoip.dat").write_bytes(b"old geoip")
    (tmp_path / "geosite.dat").write_bytes(b"old geosite")
    (tmp_path / ".xray-assets-version").write_text("stale\n", encoding="utf-8")

    def curl_result(args: list[str], **_kwargs: object) -> SimpleNamespace:
        if "-o" not in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        archive_path = Path(args[args.index("-o") + 1])
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Xray", b"new executable")
            archive.writestr("geoip.dat", b"new geoip")
            archive.writestr("geosite.dat", b"new geosite")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    real_replace = os.replace

    def fail_mid_publish(source: str | Path, destination: str | Path) -> None:
        if Path(destination).name == "geosite.dat":
            raise OSError("simulated publish failure")
        real_replace(source, destination)

    with (
        patch("meridian.xray_client._xray_bin_path", return_value=bin_path),
        patch("meridian.xray_client._resolve_asset_name", return_value="Xray-linux-64.zip"),
        patch("meridian.xray_client.subprocess.run", side_effect=curl_result),
        patch("meridian.xray_client.os.replace", side_effect=fail_mid_publish),
    ):
        assert ensure_xray_binary() is None

    assert not (tmp_path / ".xray-assets-version").exists()
    assert bin_path.read_bytes() == b"old executable"
    assert (tmp_path / "geoip.dat").read_bytes() == b"new geoip"
    assert (tmp_path / "geosite.dat").read_bytes() == b"old geosite"


def test_xray_startup_failure_returns_process_diagnostics(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = 23
    proc.stdout = io.StringIO("failed to load GeoIP: open geoip.dat: no such file")

    with (
        patch("meridian.xray_client.subprocess.Popen", return_value=proc),
        patch(
            "meridian.xray_client.poll_until_ready",
            side_effect=ReadinessTimeout("localhost:10808", 5, 2),
        ),
        pytest.raises(XrayStartupError, match="failed to load GeoIP: open geoip.dat") as exc_info,
    ):
        run_connection_test(
            tmp_path / "xray",
            {"inbounds": []},
            "",
            10808,
            "canonical endpoint",
            expect_ip_match=False,
        )

    assert "no such file" in str(exc_info.value)
    assert exc_info.value.category == "system"
    assert exc_info.value.retryable is True


def test_connection_retries_a_transient_proxy_reset(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = io.StringIO()
    responses = [
        SimpleNamespace(returncode=35, stdout="", stderr="connection reset"),
        SimpleNamespace(returncode=0, stdout="203.0.113.10\n", stderr=""),
    ]

    with (
        patch("meridian.xray_client.subprocess.Popen", return_value=proc),
        patch("meridian.xray_client.poll_until_ready"),
        patch("meridian.xray_client.subprocess.run", side_effect=responses) as curl,
        patch("meridian.xray_client.time.sleep"),
    ):
        connected, detail = run_connection_test(
            tmp_path / "xray",
            {"inbounds": []},
            "",
            10808,
            "canonical endpoint",
            expect_ip_match=False,
        )

    assert connected is True
    assert "attempt 2" in detail
    assert curl.call_count == 2


def test_connection_requires_sustained_failure(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = io.StringIO()
    failure = SimpleNamespace(returncode=35, stdout="", stderr="authentication rejected")

    with (
        patch("meridian.xray_client.subprocess.Popen", return_value=proc),
        patch("meridian.xray_client.poll_until_ready"),
        patch("meridian.xray_client.subprocess.run", return_value=failure) as curl,
        patch("meridian.xray_client.time.sleep") as sleep,
    ):
        connected, detail = run_connection_test(
            tmp_path / "xray",
            {"inbounds": []},
            "",
            10808,
            "canonical endpoint",
            expect_ip_match=False,
        )

    assert connected is False
    assert detail.endswith("after 3 attempts")
    assert curl.call_count == 3
    assert sleep.call_count == 2


def test_connection_failure_does_not_hide_xray_exit(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = 23
    proc.stdout = io.StringIO("fatal runtime error")
    failure = SimpleNamespace(returncode=35, stdout="", stderr="connection reset")

    with (
        patch("meridian.xray_client.subprocess.Popen", return_value=proc),
        patch("meridian.xray_client.poll_until_ready"),
        patch("meridian.xray_client.subprocess.run", return_value=failure),
        pytest.raises(XrayStartupError, match="fatal runtime error"),
    ):
        run_connection_test(
            tmp_path / "xray",
            {"inbounds": []},
            "",
            10808,
            "canonical endpoint",
            expect_ip_match=False,
        )


def test_missing_curl_is_a_hard_test_failure(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = io.StringIO()

    with (
        patch("meridian.xray_client.subprocess.Popen", return_value=proc),
        patch("meridian.xray_client.poll_until_ready"),
        patch(
            "meridian.xray_client.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file", "curl"),
        ),
        pytest.raises(XrayStartupError, match="command not found: curl"),
    ):
        run_connection_test(
            tmp_path / "xray",
            {"inbounds": []},
            "",
            10808,
            "canonical endpoint",
            expect_ip_match=False,
        )


# ---------------------------------------------------------------------------
# build_reality_config
# ---------------------------------------------------------------------------


class TestBuildRealityConfig:
    def test_returns_valid_structure(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="550e8400-e29b-41d4-a716-446655440000",
            sni="www.microsoft.com",
            public_key="testpubkey123",
            short_id="abcd1234",
        )
        assert "log" in config
        assert "inbounds" in config
        assert "outbounds" in config
        assert len(config["inbounds"]) == 1
        assert len(config["outbounds"]) == 1

    def test_socks_inbound_configured(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="test-uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
        )
        inbound = config["inbounds"][0]
        assert inbound["protocol"] == "socks"
        assert inbound["listen"] == "127.0.0.1"
        assert inbound["port"] == 10800

    def test_uuid_placed_correctly(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="my-test-uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
        )
        user = config["outbounds"][0]["settings"]["vnext"][0]["users"][0]
        assert user["id"] == "my-test-uuid"

    def test_server_ip_placed_correctly(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.42",
            uuid="uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
        )
        vnext = config["outbounds"][0]["settings"]["vnext"][0]
        assert vnext["address"] == "198.51.100.42"
        assert vnext["port"] == 443

    def test_reality_settings_placed_correctly(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="uuid",
            sni="example.com",
            public_key="mypublickey",
            short_id="myshortid",
        )
        reality = config["outbounds"][0]["streamSettings"]["realitySettings"]
        assert reality["publicKey"] == "mypublickey"
        assert reality["serverName"] == "example.com"
        assert reality["shortId"] == "myshortid"

    def test_default_fingerprint(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
        )
        reality = config["outbounds"][0]["streamSettings"]["realitySettings"]
        assert reality["fingerprint"] == "chrome"

    def test_custom_fingerprint(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
            fingerprint="firefox",
        )
        reality = config["outbounds"][0]["streamSettings"]["realitySettings"]
        assert reality["fingerprint"] == "firefox"

    def test_flow_is_xtls_rprx_vision(self) -> None:
        config = build_reality_config(
            socks_port=10800,
            server_ip="198.51.100.1",
            uuid="uuid",
            sni="www.microsoft.com",
            public_key="pk",
            short_id="sid",
        )
        user = config["outbounds"][0]["settings"]["vnext"][0]["users"][0]
        assert user["flow"] == "xtls-rprx-vision"


# ---------------------------------------------------------------------------
# build_xhttp_config
# ---------------------------------------------------------------------------


class TestBuildXhttpConfig:
    def test_returns_valid_structure(self) -> None:
        config = build_xhttp_config(
            socks_port=10801,
            host="198.51.100.1",
            uuid="test-uuid",
            xhttp_path="xhttppath",
        )
        assert "log" in config
        assert "inbounds" in config
        assert "outbounds" in config

    def test_path_set_correctly(self) -> None:
        config = build_xhttp_config(
            socks_port=10801,
            host="198.51.100.1",
            uuid="uuid",
            xhttp_path="myxhttppath",
        )
        stream = config["outbounds"][0]["streamSettings"]
        assert stream["network"] == "xhttp"
        assert stream["xhttpSettings"]["path"] == "/myxhttppath"

    def test_host_used_as_address_and_sni(self) -> None:
        config = build_xhttp_config(
            socks_port=10801,
            host="example.com",
            uuid="uuid",
            xhttp_path="path",
        )
        vnext = config["outbounds"][0]["settings"]["vnext"][0]
        assert vnext["address"] == "example.com"
        tls = config["outbounds"][0]["streamSettings"]["tlsSettings"]
        assert tls["serverName"] == "example.com"

    def test_ip_mode_uses_ip_as_host(self) -> None:
        config = build_xhttp_config(
            socks_port=10801,
            host="198.51.100.5",
            uuid="uuid",
            xhttp_path="path",
        )
        vnext = config["outbounds"][0]["settings"]["vnext"][0]
        assert vnext["address"] == "198.51.100.5"

    def test_socks_inbound_port(self) -> None:
        config = build_xhttp_config(
            socks_port=12345,
            host="198.51.100.1",
            uuid="uuid",
            xhttp_path="path",
        )
        assert config["inbounds"][0]["port"] == 12345

    def test_security_is_tls(self) -> None:
        config = build_xhttp_config(
            socks_port=10801,
            host="198.51.100.1",
            uuid="uuid",
            xhttp_path="path",
        )
        assert config["outbounds"][0]["streamSettings"]["security"] == "tls"


# ---------------------------------------------------------------------------
# build_wss_config
# ---------------------------------------------------------------------------


class TestBuildWssConfig:
    def test_returns_valid_structure(self) -> None:
        config = build_wss_config(
            socks_port=10802,
            domain="example.com",
            uuid="test-uuid",
            ws_path="wspath",
        )
        assert "log" in config
        assert "inbounds" in config
        assert "outbounds" in config

    def test_domain_and_path_set_correctly(self) -> None:
        config = build_wss_config(
            socks_port=10802,
            domain="example.com",
            uuid="uuid",
            ws_path="mywspath",
        )
        stream = config["outbounds"][0]["streamSettings"]
        assert stream["network"] == "ws"
        assert stream["wsSettings"]["path"] == "/mywspath"
        assert stream["wsSettings"]["headers"]["Host"] == "example.com"
        assert stream["tlsSettings"]["serverName"] == "example.com"

    def test_domain_used_as_vnext_address(self) -> None:
        config = build_wss_config(
            socks_port=10802,
            domain="vpn.example.com",
            uuid="uuid",
            ws_path="ws",
        )
        vnext = config["outbounds"][0]["settings"]["vnext"][0]
        assert vnext["address"] == "vpn.example.com"
        assert vnext["port"] == 443

    def test_socks_inbound_port(self) -> None:
        config = build_wss_config(
            socks_port=54321,
            domain="example.com",
            uuid="uuid",
            ws_path="ws",
        )
        assert config["inbounds"][0]["port"] == 54321


# ---------------------------------------------------------------------------
# build_test_configs
# ---------------------------------------------------------------------------


class TestBuildTestConfigs:
    def _make_cluster(
        self,
        *,
        ip: str = "198.51.100.1",
        sni: str = "www.microsoft.com",
        domain: str = "",
        warp: bool = False,
        public_key: str = "",
        short_id: str = "",
        ws_path: str = "",
        xhttp_path: str = "",
    ) -> ClusterConfig:
        return ClusterConfig(
            nodes=[
                NodeEntry(
                    ip=ip,
                    sni=sni,
                    domain=domain,
                    warp=warp,
                    reality_public_key=public_key,
                    reality_short_id=short_id,
                    ws_path=ws_path,
                    xhttp_path=xhttp_path,
                )
            ]
        )

    def test_empty_node_returns_empty(self) -> None:
        cluster = self._make_cluster()
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="test-uuid")
        assert configs == []

    def test_reality_only(self) -> None:
        cluster = self._make_cluster(
            public_key="testpubkey",
            short_id="abcd1234",
        )
        configs = build_test_configs_from_cluster(
            cluster,
            "198.51.100.1",
            uuid="550e8400-e29b-41d4-a716-446655440000",
        )
        assert len(configs) == 1
        label, config, expect_match = configs[0]
        assert label == "Reality (TCP)"
        assert expect_match is True  # no WARP
        assert config["outbounds"][0]["streamSettings"]["security"] == "reality"

    def test_reality_with_warp_no_ip_match(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            warp=True,
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        assert len(configs) == 1
        _, _, expect_match = configs[0]
        assert expect_match is False  # WARP: exit IP differs

    def test_wss_with_domain(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            ws_path="wspath",
            domain="example.com",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        labels = [label for label, _, _ in configs]
        assert "WSS (CDN)" in labels
        # WSS CDN never expects IP match
        wss_config = next(c for lbl, c, _ in configs if lbl == "WSS (CDN)")
        wss_match = next(m for lbl, _, m in configs if lbl == "WSS (CDN)")
        assert wss_match is False
        assert wss_config["outbounds"][0]["streamSettings"]["network"] == "ws"

    def test_wss_without_domain_not_included(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            ws_path="wspath",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        labels = [label for label, _, _ in configs]
        assert "WSS (CDN)" not in labels

    def test_xhttp_with_reality_uuid(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            xhttp_path="xhttppath",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        labels = [label for label, _, _ in configs]
        assert "XHTTP" in labels
        xhttp_config = next(c for lbl, c, _ in configs if lbl == "XHTTP")
        assert xhttp_config["outbounds"][0]["streamSettings"]["network"] == "xhttp"

    def test_xhttp_domain_mode_no_ip_match(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            xhttp_path="xhttppath",
            domain="example.com",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        xhttp_match = next(m for lbl, _, m in configs if lbl == "XHTTP")
        assert xhttp_match is False

    def test_xhttp_ip_mode_expects_match(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            xhttp_path="xhttppath",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        xhttp_match = next(m for lbl, _, m in configs if lbl == "XHTTP")
        assert xhttp_match is True

    def test_all_protocols_active(self) -> None:
        cluster = self._make_cluster(
            public_key="pk",
            short_id="sid",
            ws_path="wspath",
            xhttp_path="xhttppath",
            domain="example.com",
        )
        configs = build_test_configs_from_cluster(cluster, "198.51.100.1", uuid="uuid")
        labels = {label for label, _, _ in configs}
        assert labels == {"Reality (TCP)", "XHTTP", "WSS (CDN)"}


# ---------------------------------------------------------------------------
# _find_free_port
# ---------------------------------------------------------------------------


class TestFindFreePort:
    def test_returns_integer(self) -> None:
        port = _find_free_port()
        assert isinstance(port, int)

    def test_port_in_valid_range(self) -> None:
        port = _find_free_port()
        assert 1 <= port <= 65535

    def test_returns_different_ports(self) -> None:
        """Two consecutive calls should return different ports (not guaranteed but very likely)."""
        ports = {_find_free_port() for _ in range(5)}
        assert len(ports) > 1
