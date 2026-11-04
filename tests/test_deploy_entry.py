"""Tests for setup wizard — detect_public_ip and run() entry points.

4.0: Updated for Remnawave architecture (cluster.yml replaces proxy.yml).
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.deploy import run
from meridian.resolve import detect_public_ip, egress_address


class TestDetectPublicIP:
    @pytest.fixture(autouse=True)
    def _behind_nat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.resolve.egress_address", lambda _family, _probe: "")

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


@pytest.mark.parametrize(
    ("source", "expected"),
    [("198.51.100.20", ""), ("8.8.4.4", "8.8.4.4"), ("10.0.0.5", ""), ("100.64.0.9", "")],
    ids=["documentation-range", "global", "private-nat", "carrier-nat"],
)
def test_egress_address_keeps_only_a_global_source(monkeypatch: pytest.MonkeyPatch, source: str, expected: str) -> None:
    class FakeSocket:
        def __init__(self, *_args: object) -> None:
            pass

        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def connect(self, _address: tuple[str, int]) -> None:
            return None

        def getsockname(self) -> tuple[str, int]:
            return (source, 40000)

    monkeypatch.setattr("meridian.resolve.socket.socket", FakeSocket)

    assert egress_address(socket.AF_INET, "1.1.1.1") == expected


def test_a_public_interface_address_is_used_without_asking_an_echo_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "meridian.resolve.egress_address", lambda family, _probe: "8.8.4.4" if family == socket.AF_INET else ""
    )
    with patch("meridian.resolve.subprocess.run") as run:
        assert detect_public_ip() == "8.8.4.4"
    run.assert_not_called()


def test_a_nat_clouds_public_ipv4_wins_over_a_global_ipv6_on_its_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "meridian.resolve.egress_address",
        lambda family, _probe: "2001:4860:4860::8844" if family == socket.AF_INET6 else "",
    )
    echo = subprocess.CompletedProcess(args=[], returncode=0, stdout="198.51.100.12\n", stderr="")
    with patch("meridian.resolve.subprocess.run", return_value=echo) as run:
        assert detect_public_ip() == "198.51.100.12"
    assert run.call_args.args[0][1] == "-4"


class TestRunWithExplicitIP:
    def test_invalid_ip_exits(self) -> None:
        with pytest.raises(typer.Exit):
            run(ip="not-an-ip", yes=True)

    def test_server_name_not_found_exits(self, servers_file: Path, tmp_home: Path) -> None:
        with pytest.raises(typer.Exit):
            run(ip="nonexistent", yes=True)
