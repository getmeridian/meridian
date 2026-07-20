"""Tests for semantic provisioning ensure helpers."""

from __future__ import annotations

import pytest

from meridian.provision.ensure import ensure_service_running, ufw_rule_present
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


@pytest.mark.parametrize(
    ("rule", "rendered"),
    [
        (
            "allow 443/tcp comment meridian-v4-public",
            "ufw allow 443/tcp comment 'meridian-v4-public'",
        ),
        (
            "allow from 198.51.100.15 to any port 49684 proto tcp comment meridian-v4-private",
            "ufw allow from 198.51.100.15 to any port 49684 proto tcp comment 'meridian-v4-private'",
        ),
    ],
)
def test_ufw_rule_present_normalizes_quoted_comments(rule: str, rendered: str) -> None:
    output = f"Added user rules (see 'ufw status' for running firewall):\n{rendered}\n"

    assert ufw_rule_present(output, rule)
    assert not ufw_rule_present(output, rule.replace("meridian-v4-", "other-"))
