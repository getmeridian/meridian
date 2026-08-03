"""Shared fixtures for provisioner tests."""

from __future__ import annotations

import pytest

from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection


@pytest.fixture
def mock_conn() -> MockConnection:
    """Fresh MockConnection for each test."""
    return MockConnection()


@pytest.fixture
def base_ctx() -> ProvisionContext:
    """Minimal ProvisionContext with RFC 5737 test IP."""
    return ProvisionContext(ip="198.51.100.1")


@pytest.fixture
def domain_ctx() -> ProvisionContext:
    """ProvisionContext in domain mode."""
    return ProvisionContext(
        ip="198.51.100.1",
        domain="example.com",
    )
