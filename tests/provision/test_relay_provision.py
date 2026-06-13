"""Tests for relay node provisioning steps."""

from __future__ import annotations

import pytest

from meridian.config import REALM_SHA256, REALM_VERSION
from meridian.provision.common import ConfigureBBR, InstallPackages
from meridian.provision.relay import (
    _RELAY_PACKAGES,
    ConfigureRealm,
    ConfigureRelayFirewall,
    InstallRealm,
    RelayContext,
    VerifyRelay,
    build_relay_steps,
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
# InstallRelayPackages
# ---------------------------------------------------------------------------


class TestInstallRelayPackages:
    def test_all_present(self):
        conn = MockConnection()
        conn.when("dpkg-query", stdout="curl\nwget\nufw\nca-certificates\n")
        result = InstallPackages(packages=_RELAY_PACKAGES).run(conn, _make_ctx())
        assert result.status == "ok"

    def test_missing_triggers_install(self):
        conn = MockConnection()
        # dpkg-query returns only a subset
        conn.when("dpkg-query", stdout="curl\nwget\n")
        conn.when("apt-get update", stdout="")
        conn.when("apt-get install", stdout="")
        result = InstallPackages(packages=_RELAY_PACKAGES).run(conn, _make_ctx())
        assert result.status == "changed"
        conn.assert_called_with_pattern("apt-get install")

    def test_eol_apt_update_gives_actionable_error(self):
        """When apt-get update fails with EOL Release file error, message suggests LTS."""
        conn = MockConnection()
        conn.when("dpkg-query", stdout="curl\n")
        conn.when(
            "apt-get update",
            rc=1,
            stderr=(
                "E: The repository 'http://archive.ubuntu.com/ubuntu oracular Release' no longer has a Release file."
            ),
        )

        result = InstallPackages(packages=_RELAY_PACKAGES).run(conn, _make_ctx())

        assert result.status == "failed"
        assert "end-of-life" in result.detail
        assert "Ubuntu LTS" in result.detail


# ---------------------------------------------------------------------------
# ConfigureRelayBBR
# ---------------------------------------------------------------------------


class TestConfigureRelayBBR:
    def test_already_enabled(self):
        conn = MockConnection()
        conn.when("tcp_congestion_control", stdout="bbr")
        conn.when("default_qdisc", stdout="fq")
        result = ConfigureBBR().run(conn, _make_ctx())
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# ConfigureRelayFirewall
# ---------------------------------------------------------------------------


class TestConfigureRelayFirewall:
    def test_ufw_not_found_returns_failed(self):
        """When ufw binary is not found and install fails, status is failed."""
        conn = MockConnection()
        conn.when("which ufw", rc=1)
        conn.when("apt-get update", stdout="")
        conn.when("apt-get install", stdout="")

        result = ConfigureRelayFirewall().run(conn, _make_ctx())

        assert result.status == "failed"
        assert "ufw not available" in result.detail
        conn.assert_called_with_pattern("apt-get install")

    def test_ufw_installed_after_retry(self):
        """When ufw is missing but apt install succeeds, step continues."""
        conn = MockConnection()
        # First check (with 2>/dev/null) fails; recheck (without) succeeds
        conn.when("which ufw 2>/dev/null", rc=1)
        conn.when("which ufw", rc=0)
        conn.when("apt-get update", stdout="")
        conn.when("apt-get install", stdout="")
        conn.when("ufw status", stdout="Status: inactive")
        conn.when("ufw allow", stdout="Rule added")
        conn.when("ufw default", rc=0)
        conn.when("ufw enable", stdout="Firewall is active")

        result = ConfigureRelayFirewall().run(conn, _make_ctx())

        assert result.status == "changed"
        conn.assert_called_with_pattern("apt-get install")

    def test_already_active_returns_ok(self):
        """When ufw is present and rules already exist, status is ok."""
        conn = MockConnection()
        conn.when("which ufw", rc=0)
        conn.when("ufw status", stdout="Status: active\n22/tcp ALLOW\n443/tcp ALLOW")
        conn.when("ufw allow", stdout="Skipping adding existing rule")
        conn.when("ufw default", rc=0)
        conn.when("ufw reload", rc=0)

        result = ConfigureRelayFirewall().run(conn, _make_ctx())

        assert result.status == "ok"

    def test_allows_detected_custom_ssh_port(self):
        conn = MockConnection()
        conn.when("which ufw", rc=0)
        conn.when("sshd -T", stdout="2222\n")
        conn.when("ufw status", stdout="Status: active")
        conn.when("ufw allow", stdout="Skipping adding existing rule")
        conn.when("ufw default", rc=0)
        conn.when("ufw reload", rc=0)

        ConfigureRelayFirewall().run(conn, _make_ctx())

        conn.assert_called_with_pattern("ufw allow 2222/tcp")
        conn.assert_not_called_with_pattern("ufw allow 22/tcp")


# ---------------------------------------------------------------------------
# InstallRealm
# ---------------------------------------------------------------------------


class TestInstallRealm:
    def test_correct_version_already_installed(self):
        conn = MockConnection()
        conn.when("realm --version", stdout=f"realm {REALM_VERSION}")
        result = InstallRealm().run(conn, _make_ctx())
        assert result.status == "ok"
        assert REALM_VERSION in result.detail

    def test_downloads_when_missing(self):
        conn = MockConnection()
        # Initial check includes "2>/dev/null"; verify after install does not.
        # First-match: the specific pattern wins for the initial probe.
        conn.when("realm --version 2>/dev/null", rc=1, stderr="command not found")
        conn.when("realm --version", stdout=f"realm {REALM_VERSION}")
        conn.when("uname -m", stdout="x86_64")
        conn.when("curl", stdout="")
        conn.when("sha256sum", stdout=REALM_SHA256["x86_64-unknown-linux-gnu"])
        conn.when("tar xzf", stdout="")
        result = InstallRealm().run(conn, _make_ctx())
        assert result.status == "changed"
        conn.assert_called_with_pattern("curl")


# ---------------------------------------------------------------------------
# ConfigureRealm
# ---------------------------------------------------------------------------


class TestConfigureRealm:
    def test_writes_config_and_restarts(self):
        conn = MockConnection()
        ctx = _make_ctx()
        result = ConfigureRealm().run(conn, ctx)
        assert result.status == "changed"
        # Verify realm.toml content was written
        conn.assert_called_with_pattern("realm.toml")
        # Verify systemctl restart was called
        conn.assert_called_with_pattern("systemctl restart")
        # Verify the exit IP appears in a call (config content)
        conn.assert_called_with_pattern(ctx.exit_ip)

    def test_ipv6_exit_brackets_remote(self):
        conn = MockConnection()
        ctx = _make_ctx(exit_ip="2001:db8::2")
        result = ConfigureRealm().run(conn, ctx)
        assert result.status == "changed"
        # Verify IPv6 exit is bracketed in config
        conn.assert_called_with_pattern("[2001:db8::2]:443")

    def test_ipv6_relay_listens_dualstack(self):
        conn = MockConnection()
        ctx = _make_ctx(relay_ip="2001:db8::1", exit_ip="2001:db8::2")
        result = ConfigureRealm().run(conn, ctx)
        assert result.status == "changed"
        # Verify dual-stack listen for IPv6 relay
        conn.assert_called_with_pattern("[::]:")

    def test_ipv4_listen_unchanged(self):
        conn = MockConnection()
        ctx = _make_ctx()
        result = ConfigureRealm().run(conn, ctx)
        assert result.status == "changed"
        conn.assert_called_with_pattern("0.0.0.0:")


# ---------------------------------------------------------------------------
# VerifyRelay
# ---------------------------------------------------------------------------


class TestVerifyRelay:
    def test_service_active_exit_reachable(self):
        conn = MockConnection()
        conn.when("systemctl is-active", stdout="active")
        conn.when("nc -z", stdout="")
        result = VerifyRelay().run(conn, _make_ctx())
        assert result.status == "ok"
        assert "exit reachable" in result.detail

    def test_localhost_fallback(self):
        """When nc to exit fails, fallback to localhost succeeds."""
        conn = MockConnection()
        conn.when("systemctl is-active", stdout="active")
        # nc to exit IP fails, but nc to 127.0.0.1 succeeds
        conn.when("nc -z -w 5 198.51.100.1", rc=1)
        conn.when("nc -z -w 3 127.0.0.1", stdout="")
        result = VerifyRelay().run(conn, _make_ctx())
        assert result.status == "ok"
        assert "relay port" in result.detail

    def test_service_not_active_fails(self):
        conn = MockConnection()
        conn.when("systemctl is-active", stdout="inactive", rc=1)
        conn.when("journalctl", stdout="some error log")
        result = VerifyRelay().run(conn, _make_ctx())
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# build_relay_steps
# ---------------------------------------------------------------------------


class TestBuildRelaySteps:
    def test_returns_six_steps(self):
        ctx = _make_ctx()
        steps = build_relay_steps(ctx)
        assert len(steps) == 6
