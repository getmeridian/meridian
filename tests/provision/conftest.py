"""Shared fixtures for provisioner tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from meridian.credentials import (
    ClientEntry,
    PanelConfig,
    RealityConfig,
    ServerConfig,
    ServerCredentials,
    WSSConfig,
    XHTTPConfig,
)
from meridian.provision.steps import ProvisionContext


class MockConnection:
    """Mock ServerConnection with pattern-matching command dispatch.

    Returns different results based on command substring matching.
    First matching pattern wins. Unmatched commands return the default
    (rc=0, stdout="", stderr="").

    Usage::

        conn = MockConnection()
        conn.when("dpkg-query", stdout="curl\\tok\\n")
        conn.when("apt-get install", rc=1, stderr="broken")
        result = step.run(conn, ctx)
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, subprocess.CompletedProcess[str]]] = []
        self._calls: list[str] = []
        self._default = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        self.ip = "198.51.100.1"
        self.user = "root"
        self.local_mode = False
        self.needs_sudo = False

    def when(
        self,
        pattern: str,
        *,
        stdout: str = "",
        stderr: str = "",
        rc: int = 0,
    ) -> MockConnection:
        """Register a response for commands matching pattern (substring)."""
        self._rules.append(
            (
                pattern,
                subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr),
            )
        )
        return self

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Dispatch command to first matching pattern."""
        self._calls.append(command)
        for pattern, response in self._rules:
            if pattern in command:
                return response
        return self._default

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> list[str]:
        return list(self._calls)

    def assert_called_with_pattern(self, pattern: str) -> None:
        """Assert at least one call contained the given pattern."""
        assert any(pattern in c for c in self._calls), f"No call matching '{pattern}'. Calls: {self._calls}"

    def assert_not_called_with_pattern(self, pattern: str) -> None:
        """Assert no call contained the given pattern."""
        assert not any(pattern in c for c in self._calls), f"Unexpected call matching '{pattern}'. Calls: {self._calls}"


@pytest.fixture
def mock_conn() -> MockConnection:
    """Fresh MockConnection for each test."""
    return MockConnection()


@pytest.fixture
def base_ctx(tmp_path: Path) -> ProvisionContext:
    """Minimal ProvisionContext with RFC 5737 test IP."""
    return ProvisionContext(
        ip="198.51.100.1",
        creds_dir=str(tmp_path / "creds"),
    )


@pytest.fixture
def domain_ctx(tmp_path: Path) -> ProvisionContext:
    """ProvisionContext in domain mode."""
    return ProvisionContext(
        ip="198.51.100.1",
        domain="example.com",
        creds_dir=str(tmp_path / "creds"),
    )


def make_credentials(**overrides: object) -> ServerCredentials:
    """Factory for ServerCredentials with sensible test defaults."""
    creds = ServerCredentials(
        panel=PanelConfig(
            username="testuser",
            password="testpass",
            web_base_path="testpath",
            info_page_path="infopath",
            port=2053,
        ),
        server=ServerConfig(ip="198.51.100.1", sni="www.cloudflare.com"),
        protocols={
            "reality": RealityConfig(
                uuid="550e8400-e29b-41d4-a716-446655440000",
                private_key="test-private-key-base64",
                public_key="test-public-key-base64x",
                short_id="abcd1234",
            ),
        },
    )
    creds.protocols["wss"] = WSSConfig(
        uuid="660e8400-e29b-41d4-a716-446655440001",
        ws_path="ws789",
    )
    creds.protocols["xhttp"] = XHTTPConfig(xhttp_path="xhttp123")
    creds.clients = [
        ClientEntry(
            name="default",
            added="2026-01-01T00:00:00Z",
            reality_uuid="550e8400-e29b-41d4-a716-446655440000",
            wss_uuid="660e8400-e29b-41d4-a716-446655440001",
        )
    ]
    return creds
