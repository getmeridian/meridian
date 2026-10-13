"""Tests for relay node provisioning steps."""

from __future__ import annotations

import pytest

from meridian.config import REALM_SHA256, REALM_VERSION
from meridian.provision.relay import (
    InstallRealm,
    RelayContext,
    parse_realm_version,
)
from tests.support.mock_connection import MockConnection

# ---------------------------------------------------------------------------
# RelayContext validation
# ---------------------------------------------------------------------------


class TestRelayContextValidation:
    def test_valid_ips_accepted(self):
        ctx = RelayContext(relay_ip="198.51.100.10", exit_ip="198.51.100.1")
        assert ctx.relay_ip == "198.51.100.10"
        assert ctx.exit_ip == "198.51.100.1"

    def test_ipv6_ips_accepted(self):
        ctx = RelayContext(relay_ip="2001:db8::1", exit_ip="2001:db8::2")
        assert ctx.relay_ip == "2001:db8::1"
        assert ctx.exit_ip == "2001:db8::2"

    def test_invalid_relay_ip_raises(self):
        with pytest.raises(ValueError, match="Invalid IP address for relay_ip"):
            RelayContext(relay_ip="not-an-ip", exit_ip="198.51.100.1")

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError, match="Invalid port for listen_port"):
            RelayContext(
                relay_ip="198.51.100.10",
                exit_ip="198.51.100.1",
                listen_port=0,
            )

    def test_port_too_high_raises(self):
        with pytest.raises(ValueError, match="Invalid port for listen_port"):
            RelayContext(
                relay_ip="198.51.100.10",
                exit_ip="198.51.100.1",
                listen_port=70000,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(**overrides: object) -> RelayContext:
    """Build a RelayContext with RFC 5737 test IPs."""
    defaults = dict(relay_ip="198.51.100.10", exit_ip="198.51.100.1")
    defaults.update(overrides)
    return RelayContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InstallRealm
# ---------------------------------------------------------------------------


class TestInstallRealm:
    def test_correct_version_already_installed(self):
        conn = MockConnection()
        conn.when(
            "netfwd --version",
            stdout=f"Realm {REALM_VERSION} [brutal][batched-udp][proxy][balance][transport][multi-thread]",
        )
        result = InstallRealm().run(conn, _make_ctx())
        assert result.status == "ok"
        assert REALM_VERSION in result.detail

    def test_downloads_when_missing(self):
        conn = MockConnection()
        # Initial check includes "2>/dev/null"; verify after install does not.
        # First-match: the specific pattern wins for the initial probe.
        conn.when("netfwd --version 2>/dev/null", rc=1, stderr="command not found")
        conn.when("netfwd --version", stdout=f"Realm {REALM_VERSION} [brutal][multi-thread]")
        conn.when("uname -m", stdout="x86_64")
        conn.when("curl", stdout="")
        conn.when("sha256sum", stdout=REALM_SHA256["x86_64-unknown-linux-gnu"])
        conn.when("tar xzf", stdout="")
        result = InstallRealm().run(conn, _make_ctx())
        assert result.status == "changed"
        conn.assert_called_with_pattern("curl")

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("Realm 2.9.3 [brutal][multi-thread]\n", "2.9.3"),
            ("realm 2.9.3-rc.1+build.4\n", "2.9.3-rc.1+build.4"),
            ("other 2.9.3\n", ""),
            ("Realm [brutal] 2.9.3\n", ""),
            ("", ""),
        ],
    )
    def test_parse_realm_version(self, output: str, expected: str):
        assert parse_realm_version(output) == expected
