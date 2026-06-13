"""Shared fixtures for provisioner tests."""

from __future__ import annotations

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
from tests.support.mock_connection import MockConnection


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
        server=ServerConfig(ip="198.51.100.1", sni="www.microsoft.com"),
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
