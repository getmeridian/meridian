"""Per-hop relay health: Realm unit state, next-hop TCP from the relay, and the public listener."""

from __future__ import annotations

import re
import shlex
import time
from typing import TYPE_CHECKING

from meridian.core.relay_check import RelayHopTarget
from meridian.core.verification import VerificationCheck, VerificationFinding
from meridian.diagnostics.checks import command_evidence_unavailable
from meridian.diagnostics.evidence import finding, finish_check
from meridian.health import tcp_connect

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

_PROBE_STATUS = re.compile(r"^probe-status=(\d+)$", re.MULTILINE)


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
