"""Per-hop relay checks: unit state, next-hop TCP via bash /dev/tcp, public listener."""

from __future__ import annotations

from unittest.mock import patch

from meridian.core.relay_check import RelayHopTarget
from meridian.diagnostics.relay import check_realm_hop, check_relay_listener
from tests.support.mock_connection import MockConnection


def _hop(*, public: bool = False, legacy_service: str = "") -> RelayHopTarget:
    return RelayHopTarget(
        logical_id="realm:relay-a:1",
        chain_ref="relay-a",
        hop_index=1,
        server_ref="srv-relay-core",
        host="198.51.100.14",
        service="netfwd-abc123",
        legacy_service=legacy_service,
        listen_port=55603,
        public=public,
        next_host="198.51.100.12",
        next_port=443,
    )


def _statuses(conn: MockConnection) -> list[str]:
    return [finding.status for finding in check_realm_hop(conn, _hop()).findings]


def test_active_unit_and_open_next_hop_pass() -> None:
    conn = MockConnection().when("systemctl is-active", stdout="active\n").when("/dev/tcp", stdout="probe-status=0\n")

    check = check_realm_hop(conn, _hop())

    assert check.status == "passed"
    probe = next(command for command in conn._calls if "/dev/tcp" in command)
    assert "timeout 5 bash -c 'exec 3<>/dev/tcp/198.51.100.12/443'" in probe
    assert "nc " not in probe


def test_inactive_unit_is_negative_evidence() -> None:
    conn = MockConnection().when("systemctl is-active", stdout="inactive\n", rc=3)
    conn.when("/dev/tcp", stdout="probe-status=0\n")

    assert _statuses(conn) == ["failed", "passed"]


def test_ssh_failure_is_unavailable_evidence() -> None:
    conn = MockConnection().when("", rc=255)

    assert _statuses(conn) == ["skipped", "skipped"]


def test_next_hop_timeout_is_negative_evidence() -> None:
    conn = MockConnection().when("systemctl is-active", stdout="active\n").when("/dev/tcp", stdout="probe-status=124\n")

    check = check_realm_hop(conn, _hop())

    assert check.status == "failed"
    assert "timed out" in check.findings[1].message


def test_missing_timeout_tool_is_unavailable_evidence() -> None:
    conn = MockConnection().when("systemctl is-active", stdout="active\n").when("/dev/tcp", rc=127)

    assert _statuses(conn) == ["passed", "skipped"]


def test_public_listener_is_checked_from_this_machine() -> None:
    with patch("meridian.diagnostics.relay.tcp_connect", return_value=False) as connect:
        check = check_relay_listener(_hop(public=True))

    connect.assert_called_once_with("198.51.100.14", 55603, timeout=5)
    assert check.status == "failed"


def test_hop_still_under_its_pre_rename_unit_warns_until_the_next_apply() -> None:
    conn = MockConnection().when("is-active meridian-realm-abc123", stdout="active\n")
    conn.when("systemctl is-active", stdout="inactive\n", rc=3).when("/dev/tcp", stdout="probe-status=0\n")

    check = check_realm_hop(conn, _hop(legacy_service="meridian-realm-abc123"))

    assert [finding.code for finding in check.findings] == ["REALM_UNIT_LEGACY_NAME", "REALM_NEXT_HOP_OPEN"]
    assert check.status == "warning"
    assert "meridian apply" in check.findings[0].remediation


def test_neither_unit_active_is_negative_evidence() -> None:
    conn = MockConnection().when("systemctl is-active", stdout="inactive\n", rc=3)
    conn.when("/dev/tcp", stdout="probe-status=0\n")

    check = check_realm_hop(conn, _hop(legacy_service="meridian-realm-abc123"))

    assert check.findings[0].code == "REALM_UNIT_INACTIVE"
