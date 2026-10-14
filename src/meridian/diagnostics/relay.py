"""Per-hop relay health: Realm unit state, next-hop TCP and throughput from the relay, and the public listener."""

from __future__ import annotations

import re
import shlex
import time
from typing import TYPE_CHECKING

from meridian.core.relay_check import RelayHopTarget
from meridian.core.verification import VerificationCheck, VerificationFinding
from meridian.diagnostics.blocking import STALL_CLEARANCE_BYTES, THROTTLE_WINDOW
from meridian.diagnostics.checks import command_evidence_unavailable
from meridian.diagnostics.evidence import finding, finish_check
from meridian.health import tcp_connect

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

_PROBE_STATUS = re.compile(r"^probe-status=(\d+)$", re.MULTILINE)
_LEG_METRICS = re.compile(r"^leg=([0-9.]+)\|([0-9.]+)\|(\d+)$", re.MULTILINE)
_LEG_STATUS = re.compile(r"^leg-status=(\d+)$", re.MULTILINE)
# curl's write-out; `-w` prints even when the transfer fails, so a freeze still reports its byte count.
_LEG_FORMAT = "leg=%{time_connect}|%{time_starttransfer}|%{size_download}\\n"
_LEG_MAX_SECONDS = 20
_LEG_STALL_SECONDS = 6
_CURL_UNREACHABLE = {6, 7}
_CURL_TIMEOUT = 28
_LEG_REMEDIATION = (
    "Throttling usually targets a provider's address ranges. Try a relay host on a different provider "
    "(another ASN), or ask this provider for a new IP, then rerun `meridian check`."
)


def check_realm_hop(conn: ServerConnection, hop: RelayHopTarget, *, timeout: float = 5) -> VerificationCheck:
    """Over SSH on the hop's server: the unit is active and it can open TCP to its next hop."""
    started = time.monotonic()
    findings = [_unit_finding(conn, hop), _next_hop_finding(conn, hop, timeout=timeout)]
    return finish_check(
        f"realm_hop:{hop.chain_ref}:{hop.hop_index}",
        f"Relay {hop.chain_ref} hop {hop.hop_index + 1} on {hop.host}",
        findings,
        started,
    )


def check_relay_listener(hop: RelayHopTarget, *, timeout: float = 5) -> VerificationCheck:
    """From this machine: the relay's public entry accepts TCP."""
    started = time.monotonic()
    endpoint = f"{hop.host}:{hop.listen_port}"
    if tcp_connect(hop.host, hop.listen_port, timeout=timeout):
        result = finding("RELAY_LISTENER_OPEN", "passed", f"Public relay entry {endpoint} accepts TCP")
    else:
        result = finding(
            "RELAY_LISTENER_CLOSED",
            "failed",
            f"Public relay entry {endpoint} does not accept TCP from here",
            "Check the relay's cloud firewall and that the Realm unit is running, then run `meridian apply`.",
        )
    return finish_check(f"relay_listener:{hop.chain_ref}", f"Relay {hop.chain_ref} public entry", [result], started)


def check_relay_leg(conn: ServerConnection, hop: RelayHopTarget) -> VerificationCheck:
    """Over SSH on the hop's server: pull the camouflage site through the next hop and watch for a freeze.

    The request carries the same SNI a client sends, so it crosses this leg looking like
    client traffic; Reality hands unauthenticated handshakes to the camouflage site.
    """
    started = time.monotonic()
    check_id = f"relay_leg:{hop.chain_ref}:{hop.hop_index}"
    name = f"Relay {hop.chain_ref} hop {hop.hop_index + 1} throughput toward {hop.next_host}"
    return finish_check(check_id, name, [_leg_finding(conn, hop)], started)


def _leg_finding(conn: ServerConnection, hop: RelayHopTarget) -> VerificationFinding:
    sni = hop.camouflage_sni
    if not sni:
        return finding(
            "RELAY_LEG_UNAVAILABLE",
            "skipped",
            f"Relay {hop.chain_ref} has no Reality camouflage name to send through {hop.host}",
        )
    next_host = f"[{hop.next_host}]" if ":" in hop.next_host else hop.next_host
    connect_to = f"{sni}:443:{next_host}:{hop.next_port}"
    result = conn.run(
        "command -v curl >/dev/null 2>&1 || exit 127; "
        f"curl -sk -o /dev/null --connect-to {shlex.quote(connect_to)} --connect-timeout 5"
        f" --max-time {_LEG_MAX_SECONDS} --speed-limit 1 --speed-time {_LEG_STALL_SECONDS}"
        f" -w {shlex.quote(_LEG_FORMAT)} {shlex.quote(f'https://{sni}/')}; echo leg-status=$?",
        timeout=_LEG_MAX_SECONDS + 15,
    )
    metrics = _LEG_METRICS.search(result.stdout)
    status = _LEG_STATUS.search(result.stdout)
    if result.returncode != 0 or metrics is None or status is None:
        return finding(
            "RELAY_LEG_UNAVAILABLE",
            "skipped",
            f"Could not measure throughput from {hop.host} to {hop.next_host}:{hop.next_port}",
            "Restore SSH access to the relay and make sure curl is installed.",
        )
    connect_ms = int(float(metrics.group(1)) * 1000)
    first_byte_ms = int(float(metrics.group(2)) * 1000)
    received = int(metrics.group(3))
    exit_code = int(status.group(1))
    kilobytes = f"{received / 1024:.1f} KB"
    where = f"from {hop.host} through {hop.next_host}:{hop.next_port}"
    if exit_code in _CURL_UNREACHABLE or (exit_code == _CURL_TIMEOUT and connect_ms == 0):
        return finding(
            "RELAY_LEG_UNAVAILABLE",
            "skipped",
            f"Could not open TCP {where}; the next-hop check reports reachability",
        )
    timings = f"connect {connect_ms} ms, first byte {first_byte_ms} ms"
    if received >= STALL_CLEARANCE_BYTES:
        return finding("RELAY_LEG_OK", "passed", f"Received {kilobytes} of {sni} {where} without a freeze ({timings})")
    if exit_code != 0:
        low, high = THROTTLE_WINDOW
        message = (
            f"Transfer of {sni} {where} froze after {kilobytes} ({timings}), the signature of throttling on this leg"
            if low <= received <= high
            else f"Transfer of {sni} {where} stopped after {kilobytes} (curl exit {exit_code}, {timings})"
        )
        return finding("RELAY_LEG_STALLED", "warning", message, _LEG_REMEDIATION)
    return finding(
        "RELAY_LEG_TOO_SMALL",
        "skipped",
        f"{sni} answered with only {kilobytes} {where} ({timings}), too little to judge throttling",
        "Pick a camouflage site whose home page is larger than 32 KB to make this check conclusive.",
    )


def _unit_finding(conn: ServerConnection, hop: RelayHopTarget) -> VerificationFinding:
    result = conn.run(f"systemctl is-active {shlex.quote(hop.service)}", timeout=15)
    state = result.stdout.strip() or "unknown"
    if result.returncode == 0 and state == "active":
        return finding("REALM_UNIT_ACTIVE", "passed", f"Realm unit {hop.service} is active")
    if command_evidence_unavailable(result.returncode):
        return finding(
            "REALM_UNIT_UNKNOWN",
            "skipped",
            f"Could not inspect Realm unit {hop.service} over SSH",
            "Restore SSH access to the relay and rerun the check.",
        )
    if hop.legacy_service:
        # Hops deployed before neutral naming keep running under the old unit until the next apply.
        legacy = conn.run(f"systemctl is-active {shlex.quote(hop.legacy_service)}", timeout=15)
        if legacy.returncode == 0 and legacy.stdout.strip() == "active":
            return finding(
                "REALM_UNIT_LEGACY_NAME",
                "warning",
                f"Realm hop runs under its pre-rename unit {hop.legacy_service}, which names the project",
                "Run `meridian apply` to move the hop to neutral unit, path, and firewall names.",
            )
    return finding(
        "REALM_UNIT_INACTIVE",
        "failed",
        f"Realm unit {hop.service} is {state}",
        "Run `meridian apply` to restore the relay hop.",
    )


def _next_hop_finding(conn: ServerConnection, hop: RelayHopTarget, *, timeout: float) -> VerificationFinding:
    target = f"{hop.next_host}:{hop.next_port}"
    # bash /dev/tcp needs no extra tools (minimal images lack nc). The probe always
    # exits 0 so its own status is distinguishable from SSH or tool failures.
    seconds = max(1, int(timeout))
    probe = f"exec 3<>/dev/tcp/{hop.next_host}/{hop.next_port}"
    result = conn.run(
        f"command -v timeout >/dev/null 2>&1 || exit 127; "
        f"timeout {seconds} bash -c {shlex.quote(probe)} >/dev/null 2>&1; echo probe-status=$?",
        timeout=seconds + 15,
    )
    match = _PROBE_STATUS.search(result.stdout)
    if result.returncode != 0 or match is None:
        return finding(
            "REALM_NEXT_HOP_UNKNOWN",
            "skipped",
            f"Could not test TCP from {hop.host} to {target}",
            "Restore SSH access to the relay and make sure `timeout` and bash are installed.",
        )
    if match.group(1) == "0":
        return finding("REALM_NEXT_HOP_OPEN", "passed", f"{hop.host} reaches its next hop {target}")
    detail = "timed out" if match.group(1) == "124" else "was refused or unreachable"
    return finding(
        "REALM_NEXT_HOP_CLOSED",
        "failed",
        f"TCP from {hop.host} to its next hop {target} {detail}",
        "Check the next hop's service and firewall (it only admits the previous hop), then run `meridian apply`.",
    )
