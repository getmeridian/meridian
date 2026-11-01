"""Server-side health check primitives.

Each function takes a connection-like object (anything with ``conn.run()``)
and returns a ``CheckResult``.  No Rich, no console, no process exits.
Commands own rendering; this module owns the check logic.
"""

from __future__ import annotations

import ipaddress
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from meridian.core.verification import VerificationStatus

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

UNAVAILABLE_COMMAND_RETURN_CODES = frozenset({124, 127, 255})


def command_evidence_unavailable(returncode: int) -> bool:
    """Return whether command execution failed before producing health evidence."""
    return returncode in UNAVAILABLE_COMMAND_RETURN_CODES


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single server health check."""

    name: str
    status: VerificationStatus
    detail: str = ""
    remediation: str = ""


def check_disk_space(conn: ServerConnection, *, min_free_mb: int = 1024) -> CheckResult:
    """Check whether the root partition has at least *min_free_mb* MB free."""
    result = conn.run("df -BM --output=avail / 2>/dev/null | tail -1", timeout=15)
    if result.returncode != 0:
        return CheckResult(
            name="disk_space",
            status="skipped",
            detail="Could not query disk space",
        )

    raw = result.stdout.strip().rstrip("M")
    try:
        free_mb = int(raw)
    except (ValueError, TypeError):
        return CheckResult(
            name="disk_space",
            status="skipped",
            detail=f"Unparseable df output: {result.stdout.strip()!r}",
        )

    if free_mb < min_free_mb:
        return CheckResult(
            name="disk_space",
            status="failed",
            detail=f"{free_mb} MB free (minimum: {min_free_mb} MB)",
            remediation="Free disk space or expand the volume",
        )

    return CheckResult(
        name="disk_space",
        status="passed",
        detail=f"{free_mb} MB free",
    )


def check_port_listening(
    conn: ServerConnection,
    port: int,
    *,
    transport: Literal["tcp", "udp", "any"] = "tcp",
) -> CheckResult:
    """Check whether a port has a listener on the server."""
    flags = {"tcp": "-tlnp", "udp": "-ulnp", "any": "-tulnp"}[transport]
    q_flags = shlex.quote(flags)
    q_port = shlex.quote(str(port))
    result = conn.run(
        f"command -v ss >/dev/null 2>&1 || exit 127; ss {q_flags} sport = :{q_port} 2>/dev/null "
        "| grep -cE 'LISTEN|UNCONN'",
        timeout=10,
    )

    if command_evidence_unavailable(result.returncode):
        return CheckResult(
            name=f"port:{port}",
            status="skipped",
            detail=_unavailable_detail(result.returncode, f"Port {port} listener inspection"),
        )

    if result.returncode != 0 or result.stdout.strip() in ("", "0"):
        return CheckResult(
            name=f"port:{port}",
            status="failed",
            detail=f"Port {port} is not listening",
            remediation=f"Verify the service bound to port {port} is running",
        )

    return CheckResult(
        name=f"port:{port}",
        status="passed",
        detail=f"Port {port} is listening",
    )


def check_tls_certificate(conn: ServerConnection, host: str, *, port: int = 443) -> CheckResult:
    """Check the certificate nginx serves on the local *port*, as a client would ask for it.

    A hostname goes as SNI. An IP address goes as none, as clients send it: on an exit an
    IP SNI reaches the Reality camouflage site (so the check once judged its certificate),
    and a control server without an exit rejects it.
    """
    is_ip = _is_ip_address(host)
    server_name = "-noservername" if is_ip else f"-servername {shlex.quote(host)}"
    # Dates alone passed a certificate for another name: a panel domain's once served its IP (#110).
    name_check = f"{'-checkip' if is_ip else '-checkhost'} {shlex.quote(host)}"
    result = conn.run(
        "command -v openssl >/dev/null 2>&1 || exit 127; "
        f"echo | openssl s_client -connect 127.0.0.1:{shlex.quote(str(port))} {server_name} 2>/dev/null"
        f" | openssl x509 -noout -dates {name_check} 2>/dev/null",
        timeout=15,
    )

    if command_evidence_unavailable(result.returncode):
        return CheckResult(
            name="tls_certificate",
            status="skipped",
            detail=_unavailable_detail(result.returncode, f"TLS certificate inspection on port {port}"),
        )

    if result.returncode != 0 or not result.stdout.strip():
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail="Could not verify TLS certificate",
            remediation=f"Check that nginx is running and serving TLS on port {port}",
        )

    raw = result.stdout.strip()
    if "does NOT match certificate" in raw:
        return CheckResult(
            name="tls_certificate",
            status="failed",
            detail=f"Port {port} serves a certificate that does not name {host}",
            remediation="Run `meridian apply` to reinstall the certificate for this name",
        )
    dates = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    if "notAfter" not in dates:
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Unexpected openssl output: {raw!r}",
        )
    return _evaluate_cert_expiry(dates["notAfter"].strip(), not_before=dates.get("notBefore", "").strip())


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _unavailable_detail(returncode: int, operation: str) -> str:
    """Describe execution failures that do not prove service health."""
    if returncode == 124:
        return f"{operation} timed out"
    if returncode == 127:
        return f"{operation} is unavailable because a required tool is missing"
    return f"{operation} was interrupted by the SSH transport"


def _evaluate_cert_expiry(date_str: str, *, not_before: str = "") -> CheckResult:
    """Classify an openssl notAfter date as passed, warning, or failed.

    A long-lived certificate warns inside its last week. A short-lived one (Let's Encrypt
    IP certificates last about six days and renew with a day or so left) warns only in its
    last twentieth, or it would warn for its whole life.
    """

    def parse(value: str) -> datetime:
        return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)

    try:
        expiry = parse(date_str)
    except (ValueError, IndexError):
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Unparseable cert date: {date_str!r}",
        )
    try:
        lifetime = expiry - parse(not_before)
    except (ValueError, IndexError):
        lifetime = timedelta(days=90)

    left = expiry - datetime.now(timezone.utc)
    expiry_fmt = expiry.strftime("%Y-%m-%d")
    margin = timedelta(days=7) if lifetime >= timedelta(days=30) else lifetime / 20

    if left < timedelta(0):
        return CheckResult(
            name="tls_certificate",
            status="failed",
            detail=f"Certificate expired on {expiry_fmt}",
            remediation="Redeploy to renew the certificate: meridian deploy",
        )
    if left < margin:
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Certificate expires in {_remaining(left)} ({expiry_fmt})",
            remediation="Certificate renewal needed soon",
        )

    return CheckResult(
        name="tls_certificate",
        status="passed",
        detail=f"Valid until {expiry_fmt} ({_remaining(left)})",
    )


def _remaining(left: timedelta) -> str:
    if left.days >= 2:
        return f"{left.days} days"
    return f"{int(left.total_seconds() // 3600)} h"
