"""Per-hop relay checks: unit state, next-hop TCP via bash /dev/tcp, public listener."""

from __future__ import annotations

from unittest.mock import patch

from meridian.core.relay_check import RelayHopTarget
from meridian.diagnostics.relay import check_realm_hop, check_relay_leg, check_relay_listener
from tests.support.mock_connection import MockConnection


def _hop(
    *, public: bool = False, legacy_service: str = "", next_host: str = "198.51.100.12", sni: str = "www.example.org"
) -> RelayHopTarget:
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
        next_host=next_host,
        next_port=443,
        camouflage_sni=sni,
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


def _leg(stdout: str, *, rc: int = 0, hop: RelayHopTarget | None = None):
    conn = MockConnection().when("curl -sk", stdout=stdout, rc=rc)
    check = check_relay_leg(conn, hop or _hop())
    return check, conn


def test_leg_past_the_freeze_point_passes_with_timings() -> None:
    check, conn = _leg("leg=0.012000|0.145000|98304\nleg-status=0\n")

    assert check.status == "passed"
    assert check.id == "relay_leg:relay-a:1"
    assert "96.0 KB" in check.findings[0].message
    assert "connect 12 ms, first byte 145 ms" in check.findings[0].message
    (command,) = conn._calls
    assert "--connect-to www.example.org:443:198.51.100.12:443" in command
    assert "https://www.example.org/" in command and "--speed-time 6" in command


def test_leg_freezing_inside_the_throttle_window_warns_and_suggests_another_provider() -> None:
    check, _ = _leg("leg=0.012000|0.145000|16384\nleg-status=28\n")

    assert check.status == "warning"
    assert check.findings[0].code == "RELAY_LEG_STALLED"
    assert "signature of throttling" in check.findings[0].message
    assert "different provider" in check.findings[0].remediation


def test_leg_dying_outside_the_window_is_still_a_stall() -> None:
    check, _ = _leg("leg=0.012000|0.000000|0\nleg-status=35\n")

    assert check.status == "warning"
    assert "curl exit 35" in check.findings[0].message


def test_small_camouflage_page_is_inconclusive() -> None:
    check, _ = _leg("leg=0.012000|0.145000|2048\nleg-status=0\n")

    assert check.status == "skipped"
    assert check.findings[0].code == "RELAY_LEG_TOO_SMALL"


def test_unreachable_next_hop_defers_to_the_tcp_check() -> None:
    refused, _ = _leg("leg=0.000000|0.000000|0\nleg-status=7\n")
    timed_out, _ = _leg("leg=0.000000|0.000000|0\nleg-status=28\n")

    assert refused.status == timed_out.status == "skipped"
    assert "next-hop check" in refused.findings[0].message


def test_leg_ssh_or_missing_curl_is_unavailable_evidence() -> None:
    assert _leg("", rc=255)[0].status == "skipped"
    assert _leg("", rc=127)[0].status == "skipped"


def test_leg_brackets_an_ipv6_next_hop() -> None:
    _, conn = _leg("leg=0.012|0.145|98304\nleg-status=0\n", hop=_hop(next_host="2001:db8::12"))

    assert "--connect-to 'www.example.org:443:[2001:db8::12]:443'" in conn._calls[0]


def test_leg_without_a_camouflage_name_never_runs() -> None:
    check, conn = _leg("", hop=_hop(sni=""))

    assert check.status == "skipped"
    assert conn._calls == []
