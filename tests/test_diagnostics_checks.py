"""Tests for meridian.diagnostics — reusable server health checks.

These test the diagnostics package (meridian.diagnostics), not the
commands/diagnostics.py bug-report collector.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from meridian.diagnostics.checks import (
    check_container_running,
    check_disk_space,
    check_port_listening,
    check_tls_certificate,
)
from meridian.diagnostics.exit_dns import check_exit_dns
from meridian.diagnostics.node_plugins import check_node_plugin_host
from meridian.xray_workload import EXIT_DOH_RESOLVERS
from tests.support.mock_connection import MockConnection

# Timeout, missing tool, or a dropped SSH session is unavailable evidence, not a finding.
_UNAVAILABLE = [124, 127, 255]


def _conn(pattern: str, *, stdout: str = "", rc: int = 0) -> MockConnection:
    return MockConnection().when(pattern, stdout=stdout, rc=rc)


@pytest.mark.parametrize(
    ("stdout", "rc", "status", "detail"),
    [
        ("  2048M\n", 0, "passed", "2048"),
        ("  1024M\n", 0, "passed", ""),
        ("  512M\n", 0, "failed", "512"),
        ("", 1, "skipped", ""),
        ("  ???\n", 0, "skipped", ""),
    ],
    ids=["enough", "exact-threshold", "low", "command-failed", "unparseable"],
)
def test_disk_space(stdout: str, rc: int, status: str, detail: str) -> None:
    result = check_disk_space(_conn("df -BM", stdout=stdout, rc=rc), min_free_mb=1024)
    assert result.status == status
    assert detail in result.detail
    if status == "failed":
        assert result.remediation != ""


@pytest.mark.parametrize(
    ("stdout", "rc", "status", "detail"),
    [
        ("true\n", 0, "passed", ""),
        ("false\n", 0, "failed", "not running"),
        ("", 1, "failed", "not found"),
        *[("", rc, "skipped", "") for rc in _UNAVAILABLE],
    ],
)
def test_container_running(stdout: str, rc: int, status: str, detail: str) -> None:
    result = check_container_running(_conn("docker inspect", stdout=stdout, rc=rc), "netagent")
    assert result.name == "container:netagent"
    assert result.status == status
    assert detail in result.detail


def test_running_but_unhealthy_container_is_a_warning_not_a_pass() -> None:
    result = check_container_running(_conn("docker inspect", stdout="true unhealthy\n"), "ctlsvc-web")

    assert result.status == "warning"
    assert "docker logs" in result.remediation


@pytest.mark.parametrize(("legacy_rc", "remediation"), [(0, "meridian apply"), (1, "Deploy or restart")])
def test_missing_container_with_a_pre_rename_twin_points_at_apply(legacy_rc: int, remediation: str) -> None:
    conn = MockConnection().when("--type container remnawave-node", rc=legacy_rc).when("docker inspect", rc=1)

    result = check_container_running(conn, "netagent", "remnawave-node")

    assert result.status == "failed"
    assert remediation in result.remediation


@pytest.mark.parametrize(
    ("stdout", "rc", "status", "detail"),
    [
        ("1\n", 0, "passed", ""),
        ("0\n", 0, "failed", "not listening"),
        ("", 1, "failed", ""),
        *[("", rc, "skipped", "") for rc in _UNAVAILABLE],
    ],
)
def test_port_listening(stdout: str, rc: int, status: str, detail: str) -> None:
    result = check_port_listening(_conn("ss -tlnp", stdout=stdout, rc=rc), 8080)
    assert result.name == "port:8080"
    assert result.status == status
    assert detail in result.detail


def test_udp_listener_uses_udp_socket_evidence() -> None:
    conn = _conn("ss -ulnp", stdout="1\n")

    assert check_port_listening(conn, 8443, transport="udp").status == "passed"
    assert any("ss -ulnp" in command for command in conn.calls)


def _not_after(when: datetime) -> str:
    return f"notAfter={when.strftime('%b %d %H:%M:%S %Y GMT')}\n"


@pytest.mark.parametrize(
    ("stdout", "rc", "status", "detail"),
    [
        ("notAfter=Dec 31 23:59:59 2027 GMT\n", 0, "passed", "2027-12-31"),
        ("notAfter=Jan 01 00:00:00 2020 GMT\n", 0, "failed", "expired"),
        # Under seven days left; the exact count depends on time-of-day rounding.
        (_not_after(datetime.now(timezone.utc) + timedelta(days=3)), 0, "warning", "days"),
        ("", 1, "warning", ""),
        ("", 0, "warning", ""),
        *[("", rc, "skipped", "") for rc in _UNAVAILABLE],
    ],
    ids=["valid", "expired", "expires-soon", "cannot-connect", "empty-output", *map(str, _UNAVAILABLE)],
)
def test_tls_certificate(stdout: str, rc: int, status: str, detail: str) -> None:
    result = check_tls_certificate(_conn("openssl", stdout=stdout, rc=rc), "198.51.100.1")
    assert result.status == status
    assert detail in result.detail


def test_tls_check_quotes_the_host_and_uses_the_requested_local_port() -> None:
    conn = _conn("openssl", rc=1)

    check_tls_certificate(conn, "evil; rm -rf /", port=7443)

    assert any("'evil; rm -rf /'" in c and "127.0.0.1:7443" in c for c in conn.calls)


@pytest.mark.parametrize(
    ("host", "sni"),
    [("198.51.100.1", "-noservername"), ("vpn.example.com", "-servername vpn.example.com")],
    ids=["ip-sends-no-sni", "hostname-sends-its-sni"],
)
def test_tls_check_asks_for_the_certificate_as_a_client_would(host: str, sni: str) -> None:
    conn = _conn("openssl", stdout="notAfter=Dec 30 00:00:00 2099 GMT")

    check_tls_certificate(conn, host)

    (command,) = [c for c in conn.calls if "s_client" in c]
    assert sni in command


def _dates(issued: datetime, lifetime: timedelta) -> str:
    stamp = "%b %d %H:%M:%S %Y GMT"
    return f"notBefore={issued.strftime(stamp)}\nnotAfter={(issued + lifetime).strftime(stamp)}\n"


_NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("stdout", "status"),
    [
        (_dates(_NOW - timedelta(days=3), timedelta(days=6, hours=16)), "passed"),
        (_dates(_NOW - timedelta(days=6, hours=10), timedelta(days=6, hours=16)), "warning"),
        (_dates(_NOW - timedelta(days=85), timedelta(days=90)), "warning"),
        (_dates(_NOW - timedelta(days=30), timedelta(days=90)), "passed"),
    ],
    ids=["short-lived-mid-life", "short-lived-hours-left", "long-lived-last-week", "long-lived-mid-life"],
)
def test_certificate_expiry_warning_scales_with_its_lifetime(stdout: str, status: str) -> None:
    assert check_tls_certificate(_conn("openssl", stdout=stdout), "198.51.100.1").status == status


def _plugin_host(*, kernel: str = "6.8.0-45-generic\n", kernel_rc: int = 0) -> MockConnection:
    return MockConnection().when("uname -r", stdout=kernel, rc=kernel_rc)


@pytest.mark.parametrize(
    ("conn", "status", "codes"),
    [
        (_plugin_host(), "passed", []),
        (_plugin_host(kernel="5.7.0\n"), "passed", []),
        (_plugin_host(kernel="5.4.0-216-generic\n"), "warning", ["NODE_PLUGIN_KERNEL"]),
        *[(_plugin_host(kernel="", kernel_rc=rc), "skipped", []) for rc in _UNAVAILABLE],
        (_plugin_host(kernel="unknown\n"), "skipped", []),
    ],
    ids=["supported", "minimum", "old-kernel", *map(str, _UNAVAILABLE), "unparseable"],
)
def test_node_plugin_host_readiness(conn: MockConnection, status: str, codes: list[str]) -> None:
    check = check_node_plugin_host(conn)

    assert check.status == status
    warned = [found for found in check.findings if found.status == "warning"]
    assert [found.code for found in warned] == codes
    # Plugins are optional: an unready host warns with a fix and never fails the check.
    assert all(found.remediation.startswith("Only if you use node plugins") for found in warned)


_IPV4_RESOLVERS = {resolver.address for resolver in EXIT_DOH_RESOLVERS if ":" not in resolver.address}
_IPV6_RESOLVERS = {resolver.address for resolver in EXIT_DOH_RESOLVERS} - _IPV4_RESOLVERS
_PRIMARY = EXIT_DOH_RESOLVERS[0]


def _dns_host(answering: set[str], *, rc: int = 0, drop: str = "") -> MockConnection:
    lines = [
        f"{resolver.url} {'200' if resolver.address in answering else '000'}"
        for resolver in EXIT_DOH_RESOLVERS
        if resolver.address != drop
    ]
    return MockConnection().when("dns-query", stdout="\n".join(lines) + "\n", rc=rc)


@pytest.mark.parametrize(
    ("conn", "status"),
    [
        (_dns_host(_IPV4_RESOLVERS | _IPV6_RESOLVERS), "passed"),
        # Single-stack hosts still reach every operator over their own family.
        (_dns_host(_IPV4_RESOLVERS), "passed"),
        (_dns_host(_IPV6_RESOLVERS), "passed"),
        (_dns_host(_IPV4_RESOLVERS - {_PRIMARY.address}), "warning"),
        (_dns_host(set()), "failed"),
        *[(_dns_host(set(), rc=rc), "skipped") for rc in _UNAVAILABLE],
        (_dns_host(_IPV4_RESOLVERS, drop=_PRIMARY.address), "skipped"),
    ],
    ids=["dual-stack", "ipv4-only", "ipv6-only", "one-operator-silent", "none", *map(str, _UNAVAILABLE), "partial"],
)
def test_exit_dns_judges_reachability_per_operator(conn: MockConnection, status: str) -> None:
    check = check_exit_dns(conn)

    assert check.status == status
    found = check.findings[0]
    if status == "warning":
        assert _PRIMARY.operator in found.message
        assert _PRIMARY.address in found.remediation
    if status == "failed":
        # Say what to open, and that nothing falls back to plain DNS meanwhile.
        assert "plain DNS" in found.message
        assert all(resolver.address in found.remediation for resolver in EXIT_DOH_RESOLVERS)


def test_exit_dns_asks_every_endpoint_by_address_over_https() -> None:
    conn = _dns_host(_IPV4_RESOLVERS)

    check_exit_dns(conn)

    assert len(conn.calls) == 1
    for resolver in EXIT_DOH_RESOLVERS:
        assert f"{resolver.url}?dns=" in conn.calls[0]
    assert "application/dns-message" in conn.calls[0]
