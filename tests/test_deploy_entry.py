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

    def test_server_name_not_found_exits(self, servers_file: Path, tmp_home: Path) -> None:
        """An unknown name that is not an address should fail."""
        with pytest.raises(typer.Exit):
            run(ip="nonexistent", yes=True)
