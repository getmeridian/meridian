"""Server-side health check primitives.

Each function takes a connection-like object (anything with ``conn.run()``)
and returns a ``CheckResult``.  No Rich, no console, no process exits.
Commands own rendering; this module owns the check logic.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

CheckStatus = Literal["passed", "failed", "warning", "skipped"]
UNAVAILABLE_COMMAND_RETURN_CODES = frozenset({124, 127, 255})


def command_evidence_unavailable(returncode: int) -> bool:
    """Return whether command execution failed before producing health evidence."""
    return returncode in UNAVAILABLE_COMMAND_RETURN_CODES


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single server health check."""

    name: str
    status: CheckStatus
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


def check_container_running(conn: ServerConnection, container_name: str) -> CheckResult:
    """Check whether a Docker container is running by name."""
    q_name = shlex.quote(container_name)
    result = conn.run(
        "command -v docker >/dev/null 2>&1 || exit 127; "
        f"docker inspect -f '{{{{.State.Running}}}}' {q_name} 2>/dev/null",
        timeout=15,
    )

    if command_evidence_unavailable(result.returncode):
        return CheckResult(
            name=f"container:{container_name}",
            status="skipped",
            detail=_unavailable_detail(result.returncode, "Docker container inspection"),
        )

    if result.returncode != 0:
        return CheckResult(
            name=f"container:{container_name}",
            status="failed",
            detail=f"Container {container_name!r} not found",
            remediation=f"Deploy or restart the {container_name} container",
        )

    running = result.stdout.strip() == "true"
    if running:
        return CheckResult(
            name=f"container:{container_name}",
            status="passed",
            detail=f"{container_name} is running",
        )

    return CheckResult(
        name=f"container:{container_name}",
        status="failed",
        detail=f"{container_name} exists but is not running",
        remediation=f"docker start {container_name}",
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
    """Check TLS certificate validity and expiry via openssl on the server.

    Connects to the local *port* with the given *host* as SNI to inspect
    the certificate that nginx serves locally.
    """
    q_host = shlex.quote(host)
    q_port = shlex.quote(str(port))
    result = conn.run(
        "command -v openssl >/dev/null 2>&1 || exit 127; "
        f"echo | openssl s_client -connect 127.0.0.1:{q_port} -servername {q_host} 2>/dev/null"
        " | openssl x509 -noout -enddate 2>/dev/null",
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
    if "notAfter" not in raw:
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Unexpected openssl output: {raw!r}",
        )

    date_str = raw.split("=", 1)[1].strip()
    return _evaluate_cert_expiry(date_str)


def _unavailable_detail(returncode: int, operation: str) -> str:
    """Describe execution failures that do not prove service health."""
    if returncode == 124:
        return f"{operation} timed out"
    if returncode == 127:
        return f"{operation} is unavailable because a required tool is missing"
    return f"{operation} was interrupted by the SSH transport"


def _evaluate_cert_expiry(date_str: str) -> CheckResult:
    """Parse an openssl notAfter date and classify as passed/warning/failed."""
    from datetime import datetime, timezone

    try:
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Unparseable cert date: {date_str!r}",
        )

    now = datetime.now(timezone.utc)
    days_left = (expiry - now).days
    expiry_fmt = expiry.strftime("%Y-%m-%d")

    if days_left < 0:
        return CheckResult(
            name="tls_certificate",
            status="failed",
            detail=f"Certificate expired on {expiry_fmt}",
            remediation="Redeploy to renew the certificate: meridian deploy",
        )
    if days_left < 7:
        return CheckResult(
            name="tls_certificate",
            status="warning",
            detail=f"Certificate expires in {days_left} days ({expiry_fmt})",
            remediation="Certificate renewal needed soon",
        )

    return CheckResult(
        name="tls_certificate",
        status="passed",
        detail=f"Valid until {expiry_fmt} ({days_left} days)",
    )


def check_firewall_port(conn: ServerConnection, port: int) -> CheckResult:
    """Check whether UFW allows traffic on the given TCP port."""
    # First check if UFW is installed and active
    which_result = conn.run("which ufw 2>/dev/null", timeout=10)
    if which_result.returncode != 0:
        return CheckResult(
            name=f"firewall:{port}",
            status="skipped",
            detail="UFW is not installed",
        )

    status_result = conn.run("ufw status", timeout=10)
    if status_result.returncode != 0 or "Status: active" not in status_result.stdout:
        return CheckResult(
            name=f"firewall:{port}",
            status="skipped",
            detail="UFW is not active",
        )

    # Check if port is allowed — match port at word boundary to avoid
    # 80 matching 8080, 22 matching 2222, etc.
    raw = status_result.stdout
    port_str = str(port)
    for line in raw.splitlines():
        stripped = line.strip()
        if "ALLOW" not in stripped:
            continue
        # UFW formats: "443/tcp  ALLOW", "443  ALLOW", "22/tcp (v6)  ALLOW"
        rule_port = stripped.split("/")[0].split()[0] if "/" in stripped else stripped.split()[0]
        if rule_port == port_str:
            return CheckResult(
                name=f"firewall:{port}",
                status="passed",
                detail=f"Port {port}/tcp is allowed through UFW",
            )

    return CheckResult(
        name=f"firewall:{port}",
        status="failed",
        detail=f"Port {port}/tcp is not allowed through UFW",
        remediation=f"ufw allow {port}/tcp",
    )
