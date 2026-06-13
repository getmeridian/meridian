"""Tests for semantic provisioning ensure helpers."""

from __future__ import annotations

from meridian.provision.ensure import ensure_service_running
from tests.support.mock_connection import MockConnection


def test_ensure_service_running_enables_active_but_disabled_service() -> None:
    conn = (
        MockConnection()
        .when("systemctl is-active docker", stdout="active\n")
        .when("systemctl is-enabled docker", stdout="disabled\n", rc=1)
        .when("systemctl enable docker")
    )

    result = ensure_service_running(conn, "docker")

    assert result.ok is True
    assert result.changed is True
    assert "systemctl enable docker" in conn.calls
    assert not any("systemctl start docker" in call for call in conn.calls)


def test_ensure_service_running_skips_enabled_active_service() -> None:
    conn = (
        MockConnection()
        .when("systemctl is-active docker", stdout="active\n")
        .when("systemctl is-enabled docker", stdout="enabled\n")
    )

    result = ensure_service_running(conn, "docker")

    assert result.ok is True
    assert result.changed is False
    assert not any("systemctl enable docker" in call for call in conn.calls)
    assert not any("systemctl start docker" in call for call in conn.calls)
