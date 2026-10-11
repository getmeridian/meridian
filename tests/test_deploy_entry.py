"""Tests for setup wizard — detect_public_ip and run() entry points.

4.0: Updated for Remnawave architecture (cluster.yml replaces proxy.yml).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.deploy import run
from meridian.resolve import detect_public_ip


class TestDetectPublicIP:
    def test_returns_valid_ip(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="198.51.100.10\n", stderr="")
        with patch("meridian.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == "198.51.100.10"

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
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="198.51.100.11\n", stderr="")

        with patch("meridian.resolve.subprocess.run", side_effect=side_effect):
            ip = detect_public_ip()
        assert ip == "198.51.100.11"
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
            run(ip="198.51.100.10", requested_server="mybox", yes=True)

    def test_server_flag_resolves_ip_from_registry(self, servers_file: Path, tmp_home: Path) -> None:
        """--server flag with a known name should resolve to its IP."""
        from meridian.servers import ServerEntry, ServerRegistry

        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "prod"))

        entry = reg.find("prod")
        assert entry is not None
        assert entry.host == "198.51.100.10"
        assert entry.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path, tmp_home: Path) -> None:
        """--server with unknown name and non-IP string should fail."""
        with pytest.raises(typer.Exit):
            run(requested_server="nonexistent", yes=True)
