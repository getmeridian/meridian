"""Tests for meridian.diagnostics — reusable server health checks.

These test the diagnostics package (meridian.diagnostics), not the
commands/diagnostics.py bug-report collector. Uses MockConnection
for all checks.
"""

from __future__ import annotations

import pytest

from meridian.diagnostics import (
    CheckResult,
    check_container_running,
    check_disk_space,
    check_firewall_port,
    check_port_listening,
    check_tls_certificate,
)
from tests.support.mock_connection import MockConnection

# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------


class TestCheckDiskSpace:
    def test_passed_when_enough_space(self) -> None:
        conn = MockConnection()
        conn.when("df -BM", stdout="  2048M\n")
        result = check_disk_space(conn, min_free_mb=1024)
        assert result.status == "passed"
        assert "2048" in result.detail

    def test_failed_when_low_space(self) -> None:
        conn = MockConnection()
        conn.when("df -BM", stdout="  512M\n")
        result = check_disk_space(conn, min_free_mb=1024)
        assert result.status == "failed"
        assert "512" in result.detail
        assert result.remediation != ""

    def test_skipped_when_command_fails(self) -> None:
        conn = MockConnection()
        conn.when("df -BM", rc=1)
        result = check_disk_space(conn)
        assert result.status == "skipped"

    def test_skipped_when_unparseable_output(self) -> None:
        conn = MockConnection()
        conn.when("df -BM", stdout="  ???\n")
        result = check_disk_space(conn)
        assert result.status == "skipped"

    def test_exact_threshold_passes(self) -> None:
        conn = MockConnection()
        conn.when("df -BM", stdout="  1024M\n")
        result = check_disk_space(conn, min_free_mb=1024)
        assert result.status == "passed"


# ---------------------------------------------------------------------------
# check_container_running
# ---------------------------------------------------------------------------


class TestCheckContainerRunning:
    def test_passed_when_running(self) -> None:
        conn = MockConnection()
        conn.when("docker inspect", stdout="true\n")
        result = check_container_running(conn, "remnawave-node")
        assert result.status == "passed"
        assert result.name == "container:remnawave-node"

    def test_failed_when_not_running(self) -> None:
        conn = MockConnection()
        conn.when("docker inspect", stdout="false\n")
        result = check_container_running(conn, "remnawave-node")
        assert result.status == "failed"
        assert "not running" in result.detail

    def test_failed_when_container_not_found(self) -> None:
        conn = MockConnection()
        conn.when("docker inspect", rc=1)
        result = check_container_running(conn, "remnawave-node")
        assert result.status == "failed"
        assert "not found" in result.detail

    @pytest.mark.parametrize("returncode", [124, 127, 255])
    def test_skipped_when_container_evidence_is_unavailable(self, returncode: int) -> None:
        conn = MockConnection()
        conn.when("docker inspect", rc=returncode)

        result = check_container_running(conn, "remnawave-node")

        assert result.status == "skipped"


# ---------------------------------------------------------------------------
# check_port_listening
# ---------------------------------------------------------------------------


class TestCheckPortListening:
    def test_passed_when_listening(self) -> None:
        conn = MockConnection()
        conn.when("ss -tlnp", stdout="1\n")
        result = check_port_listening(conn, 443)
        assert result.status == "passed"
        assert result.name == "port:443"

    def test_failed_when_not_listening(self) -> None:
        conn = MockConnection()
        conn.when("ss -tlnp", stdout="0\n")
        result = check_port_listening(conn, 443)
        assert result.status == "failed"
        assert "not listening" in result.detail

    def test_failed_when_command_fails(self) -> None:
        conn = MockConnection()
        conn.when("ss -tlnp", rc=1)
        result = check_port_listening(conn, 8080)
        assert result.status == "failed"
        assert result.name == "port:8080"

    @pytest.mark.parametrize("returncode", [124, 127, 255])
    def test_skipped_when_listener_evidence_is_unavailable(self, returncode: int) -> None:
        conn = MockConnection()
        conn.when("ss -tlnp", rc=returncode)

        result = check_port_listening(conn, 8080)

        assert result.status == "skipped"

    def test_udp_listener_uses_udp_socket_evidence(self) -> None:
        conn = MockConnection()
        conn.when("ss -ulnp", stdout="1\n")

        result = check_port_listening(conn, 8443, transport="udp")

        assert result.status == "passed"
        assert any("ss -ulnp" in command for command in conn.calls)


# ---------------------------------------------------------------------------
# check_tls_certificate
# ---------------------------------------------------------------------------


class TestCheckTlsCertificate:
    def test_passed_when_cert_valid(self) -> None:
        conn = MockConnection()
        conn.when("openssl", stdout="notAfter=Dec 31 23:59:59 2027 GMT\n")
        result = check_tls_certificate(conn, "198.51.100.1")
        assert result.status == "passed"
        assert "2027-12-31" in result.detail

    def test_warning_when_cannot_connect(self) -> None:
        conn = MockConnection()
        conn.when("openssl", rc=1)
        result = check_tls_certificate(conn, "198.51.100.1")
        assert result.status == "warning"

    def test_warning_when_empty_output(self) -> None:
        conn = MockConnection()
        conn.when("openssl", stdout="")
        result = check_tls_certificate(conn, "198.51.100.1")
        assert result.status == "warning"

    @pytest.mark.parametrize("returncode", [124, 127, 255])
    def test_skipped_when_tls_evidence_is_unavailable(self, returncode: int) -> None:
        conn = MockConnection()
        conn.when("openssl", rc=returncode)

        result = check_tls_certificate(conn, "198.51.100.1")

        assert result.status == "skipped"

    def test_failed_when_cert_expired(self) -> None:
        conn = MockConnection()
        conn.when("openssl", stdout="notAfter=Jan 01 00:00:00 2020 GMT\n")
        result = check_tls_certificate(conn, "198.51.100.1")
        assert result.status == "failed"
        assert "expired" in result.detail.lower()

    def test_warning_when_cert_expires_soon(self) -> None:
        """Certificate expiring within 7 days triggers warning."""
        from datetime import datetime, timedelta, timezone

        soon = datetime.now(timezone.utc) + timedelta(days=3)
        date_str = soon.strftime("%b %d %H:%M:%S %Y GMT")

        conn = MockConnection()
        conn.when("openssl", stdout=f"notAfter={date_str}\n")
        result = check_tls_certificate(conn, "198.51.100.1")
        assert result.status == "warning"
        # days_left depends on time-of-day rounding; just verify it's under 7
        assert "days" in result.detail

    def test_uses_shlex_quote_on_host(self) -> None:
        """Verify the host argument is safely quoted in the shell command."""
        conn = MockConnection()
        conn.when("openssl", rc=1)
        check_tls_certificate(conn, "evil; rm -rf /")
        # Verify the command was called with a quoted host
        assert any("'evil; rm -rf /'" in c for c in conn.calls)

    def test_uses_requested_local_tls_port(self) -> None:
        conn = MockConnection()
        conn.when("openssl", rc=1)

        check_tls_certificate(conn, "edge.example.com", port=7443)

        assert any("127.0.0.1:7443" in command for command in conn.calls)


# ---------------------------------------------------------------------------
# check_firewall_port
# ---------------------------------------------------------------------------


class TestCheckFirewallPort:
    def test_skipped_when_ufw_not_installed(self) -> None:
        conn = MockConnection()
        conn.when("which ufw", rc=1)
        result = check_firewall_port(conn, 443)
        assert result.status == "skipped"
        assert "not installed" in result.detail

    def test_skipped_when_ufw_inactive(self) -> None:
        conn = MockConnection()
        conn.when("which ufw", stdout="/usr/sbin/ufw\n")
        conn.when("ufw status", stdout="Status: inactive\n")
        result = check_firewall_port(conn, 443)
        assert result.status == "skipped"
        assert "not active" in result.detail

    def test_passed_when_port_allowed(self) -> None:
        conn = MockConnection()
        conn.when("which ufw", stdout="/usr/sbin/ufw\n")
        conn.when(
            "ufw status",
            stdout=(
                "Status: active\n"
                "\n"
                "To                         Action      From\n"
                "--                         ------      ----\n"
                "443/tcp                    ALLOW       Anywhere\n"
            ),
        )
        result = check_firewall_port(conn, 443)
        assert result.status == "passed"

    def test_failed_when_port_not_allowed(self) -> None:
        conn = MockConnection()
        conn.when("which ufw", stdout="/usr/sbin/ufw\n")
        conn.when(
            "ufw status",
            stdout=(
                "Status: active\n"
                "\n"
                "To                         Action      From\n"
                "--                         ------      ----\n"
                "22/tcp                     ALLOW       Anywhere\n"
            ),
        )
        result = check_firewall_port(conn, 443)
        assert result.status == "failed"
        assert result.remediation != ""


# ---------------------------------------------------------------------------
# CheckResult contract
# ---------------------------------------------------------------------------


class TestCheckResultContract:
    def test_frozen_dataclass(self) -> None:
        r = CheckResult(name="test", status="passed", detail="ok")
        assert r.name == "test"
        assert r.status == "passed"
        assert r.detail == "ok"
        assert r.remediation == ""

    def test_all_statuses_accepted(self) -> None:
        for status in ("passed", "failed", "warning", "skipped"):
            r = CheckResult(name="x", status=status)
            assert r.status == status
