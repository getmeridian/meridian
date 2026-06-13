"""Tests for setup wizard — detect_public_ip and run() entry points.

4.0: Updated for Remnawave architecture (cluster.yml replaces proxy.yml).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from meridian.commands.setup import (
    _build_redeploy_command,
    run,
)
from meridian.config import is_ipv4
from meridian.resolve import detect_public_ip


class TestDetectPublicIP:
    def test_returns_valid_ip(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="93.184.216.34\n", stderr="")
        with patch("meridian.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == "93.184.216.34"
        assert is_ipv4(ip)

    def test_returns_empty_on_timeout(self) -> None:
        with patch(
            "meridian.resolve.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=5),
        ):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_not_found(self) -> None:
        with patch(
            "meridian.resolve.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_invalid_output(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="not-an-ip\n", stderr="")
        with patch("meridian.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_curl_failure(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch("meridian.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == ""

    def test_tries_fallback_url(self) -> None:
        """If first URL fails, should try the fallback."""
        calls = []

        def side_effect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            calls.append(args)
            if len(calls) == 1:
                return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="10.0.0.1\n", stderr="")

        with patch("meridian.resolve.subprocess.run", side_effect=side_effect):
            ip = detect_public_ip()
        assert ip == "10.0.0.1"
        assert len(calls) == 2


class TestRunWithExplicitIP:
    """Test run() behavior when IP is provided explicitly (non-interactive)."""

    def test_invalid_ip_exits(self) -> None:
        """run() with invalid IP should fail."""
        with pytest.raises(typer.Exit):
            run(ip="not-an-ip", yes=True)

    def test_both_ip_and_server_flag_fails(self, tmp_home: Path) -> None:
        """Cannot use both positional IP and --server flag."""
        with pytest.raises(typer.Exit):
            run(ip="1.2.3.4", requested_server="mybox", yes=True)

    def test_server_flag_resolves_ip_from_registry(self, servers_file: Path, tmp_home: Path) -> None:
        """--server flag with a known name should resolve to its IP."""
        from meridian.servers import ServerEntry, ServerRegistry

        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("10.20.30.40", "root", "prod"))

        entry = reg.find("prod")
        assert entry is not None
        assert entry.host == "10.20.30.40"
        assert entry.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path, tmp_home: Path) -> None:
        """--server with unknown name and non-IP string should fail."""
        with pytest.raises(typer.Exit):
            run(requested_server="nonexistent", yes=True)


class TestIsIPv4:
    """Test the is_ipv4 helper used by setup."""

    def test_valid_ips(self) -> None:
        assert is_ipv4("1.2.3.4") is True
        assert is_ipv4("255.255.255.255") is True
        assert is_ipv4("0.0.0.0") is True
        assert is_ipv4("192.168.1.1") is True

    def test_invalid_ips(self) -> None:
        assert is_ipv4("") is False
        assert is_ipv4("256.1.1.1") is False
        assert is_ipv4("1.2.3") is False
        assert is_ipv4("1.2.3.4.5") is False
        assert is_ipv4("abc.def.ghi.jkl") is False
        assert is_ipv4("not-an-ip") is False
        assert is_ipv4("1.2.3.-1") is False


class TestBuildRedeployCommand:
    def test_minimal_defaults(self) -> None:
        resolved = SimpleNamespace(ip="1.2.3.4", user="root")
        cmd = _build_redeploy_command(
            resolved,
            sni="",
            domain="",
            client_name="default",
            harden=True,
            server_name="",
            icon="",
            color="",
            warp=False,
            geo_block=True,
        )
        assert cmd == "meridian deploy 1.2.3.4 --yes"

    def test_full_options(self) -> None:
        resolved = SimpleNamespace(ip="198.51.100.1", user="armenqa")
        cmd = _build_redeploy_command(
            resolved,
            sni="cdn.example.net",
            domain="vpn.example.com",
            client_name="ARMIK",
            harden=False,
            server_name="My VPN",
            icon="",
            color="sunset",
            warp=True,
            geo_block=False,
        )
        assert "198.51.100.1" in cmd
        assert "--user armenqa" in cmd
        assert "--sni cdn.example.net" in cmd
        assert "--domain vpn.example.com" in cmd
        assert "--client-name ARMIK" in cmd
        assert "--no-harden" in cmd
        assert "--warp" in cmd
        assert "--no-geo-block" in cmd
        assert "--display-name 'My VPN'" in cmd
        assert "--color sunset" in cmd
        assert "--yes" in cmd

    def test_default_sni_omitted(self) -> None:
        from meridian.config import DEFAULT_SNI

        resolved = SimpleNamespace(ip="1.2.3.4", user="root")
        cmd = _build_redeploy_command(
            resolved,
            sni=DEFAULT_SNI,
            domain="",
            client_name="",
            harden=True,
            server_name="",
            icon="",
            color="ocean",
            warp=False,
            geo_block=True,
        )
        assert "--sni" not in cmd
        assert "--color" not in cmd
