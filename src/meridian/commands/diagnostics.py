"""System diagnostics collection for bug reports."""

from __future__ import annotations

import platform
import re
import shlex

from meridian.cluster import ClusterConfig
from meridian.commands.resolve import (
    ensure_server_connection,
    fetch_credentials,
    resolve_server,
)
from meridian.config import DEFAULT_SNI, SERVERS_FILE
from meridian.console import err_console, line
from meridian.facts import ServerFacts
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection


def run(
    ip: str = "",
    sni: str = "",
    user: str = "root",
    ai: bool = False,
    requested_server: str = "",
) -> None:
    """Collect system info from the server for bug reports. Redacts secrets."""
    registry = ServerRegistry(SERVERS_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    resolved = ensure_server_connection(resolved)
    fetch_credentials(resolved)

    from meridian import __version__

    err_console.print()
    err_console.print("  [bold]Meridian Diagnostics[/bold]")
    err_console.print("  [dim]Collecting system info for bug reports...[/dim]")
    err_console.print("  [warn]Note: secrets (passwords, UUIDs, keys) are redacted.[/warn]")
    err_console.print()

    sections: list[tuple[str, str]] = []

    def _collect(cmd: str, timeout: int = 10, fallback: str = "unknown") -> str:
        """Run a remote command, return output with the command as a comment header."""
        output = resolved.conn.run(cmd, timeout=timeout).stdout.strip() or fallback
        return f"$ {cmd}\n{output}"

    # --- Local Machine ---
    local_os = platform.platform()

    # Check deployed version
    cluster = ClusterConfig.load()
    node = cluster.find_node(resolved.ip) if cluster.is_configured else None
    deployed_with = (node.deployed_with if node else "") or "unknown"

    local_info = f"OS: {local_os}\nMeridian: {__version__}\nServer deployed with: {deployed_with}"

    sections.append(
        (
            "Local Machine",
            local_info,
        )
    )

    # --- Deployment context (for AI enrichment) ---
    domain = (node.domain if node else "") or ""
    mode = "domain" if domain else "IP"
    has_relays = bool(cluster.relays) if cluster.is_configured else False
    if has_relays:
        mode += f" + {len(cluster.relays)} relay(s)"
    protocols = ["Reality"]
    if node and node.domain:
        protocols.append("XHTTP")
        protocols.append("WSS")

    deployment_info = f"Mode: {mode}\nProtocols: {', '.join(protocols)}"
    sections.append(("Deployment", deployment_info))

    facts = ServerFacts(resolved.conn)
    os_release = facts.os_release()
    docker = facts.docker_state()
    ufw = facts.ufw_state()
    fact_parts = [
        f"OS: {os_release.pretty_name or os_release.id}",
        f"Architecture: {facts.arch() or 'unknown'} / dpkg {facts.dpkg_arch()}",
        f"SSH ports: {', '.join(str(p) for p in facts.ssh_ports())}",
        f"Free disk: {facts.free_disk_mb('/') or 'unknown'} MB",
        f"Docker: {'installed' if docker.installed else 'not installed'}"
        + (f" ({docker.version})" if docker.version else ""),
        f"Docker compose: {'available' if docker.compose_available else 'missing'}",
        f"UFW: {'active' if ufw.active else 'inactive' if ufw.installed else 'not installed'}",
    ]
    sections.append(("Server Facts", "\n".join(fact_parts)))

    # --- Server info ---
    server_parts = [
        _collect("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME"),
        _collect("uname -r"),
        _collect("uptime"),
        _collect("df -h / 2>/dev/null | tail -1"),
        _collect("free -h 2>/dev/null | grep Mem"),
    ]
    sections.append(("Server", "\n".join(server_parts)))

    # --- Docker ---
    docker_parts = [
        _collect("docker --version 2>&1", fallback="not installed"),
        _collect("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1", fallback="no containers"),
    ]
    sections.append(("Docker", "\n".join(docker_parts)))

    # --- Xray process ---
    xray_pid_raw = resolved.conn.run("docker exec remnawave-node pgrep -f xray 2>/dev/null", timeout=10).stdout.strip()
    if xray_pid_raw:
        xray_status = f"running (PID {xray_pid_raw.splitlines()[0]})"
    else:
        xray_status = "NOT RUNNING — proxy traffic is not flowing"
    sections.append(("Xray Process", xray_status))

    # --- Node capabilities ---
    from meridian.capabilities import detect_capabilities

    caps = detect_capabilities(resolved.conn)
    cap_lines = [f"Xray version: {caps.xray_version or 'unknown'}"]
    enabled = [k for k in ("reality", "xhttp", "wss", "hysteria2", "finalmask", "vless_pq") if getattr(caps, k)]
    cap_lines.append(f"Enabled: {', '.join(enabled) if enabled else 'none detected'}")
    sections.append(("Node Capabilities", "\n".join(cap_lines)))

    # --- Remnawave Node Logs (redacted) ---
    log_cmd = "docker logs remnawave-node --tail 50 2>&1 | grep -v '^\\s*$' | sort -u | tail -20"
    xray_logs = resolved.conn.run(log_cmd, timeout=15).stdout.strip() or "container not running"
    xray_logs = _redact_secrets(xray_logs)
    sections.append(("Remnawave Node Logs", f"$ {log_cmd}\n{xray_logs}"))

    # --- Nginx error log ---
    nginx_cmd = "tail -10 /var/log/nginx/error.log 2>/dev/null"
    nginx_errors = resolved.conn.run(nginx_cmd, timeout=10).stdout.strip() or "no errors or log not found"
    nginx_errors = _redact_secrets(nginx_errors)
    sections.append(("Nginx Errors", f"$ {nginx_cmd}\n{nginx_errors}"))

    # --- TLS certificate expiry ---
    cert_expiry = _check_cert_expiry(resolved.conn)
    sections.append(("TLS Certificate", cert_expiry))

    # --- Listening Ports ---
    sections.append(
        (
            "Listening Ports",
            _collect(
                "ss -tlnp sport = :443 or sport = :80 or sport = :8443 or sport = :8444 2>&1",
            ),
        )
    )

    # --- Firewall ---
    sections.append(("Firewall (UFW)", _collect("ufw status verbose 2>&1", fallback="ufw not available")))

    # --- Geo-blocking ---
    geo_status = "managed by Remnawave (check panel settings)"
    sections.append(("Geo-blocking", geo_status))

    # --- SNI Target ---
    sni_host = sni or (node.sni if node else "") or DEFAULT_SNI
    q_sni = shlex.quote(sni_host)
    sni_cmd = (
        f"echo | openssl s_client -connect {q_sni}:443 -servername {q_sni} 2>/dev/null "
        f"| grep -E 'subject=|issuer=|CONNECTED'"
    )
    sni_check = resolved.conn.run(sni_cmd, timeout=10).stdout.strip() or "unreachable"
    sections.append((f"Camouflage Target ({sni_host})", f"$ {sni_cmd}\n{sni_check}"))

    # --- Domain DNS ---
    if node and node.domain:
        q_domain = shlex.quote(node.domain)
        sections.append(
            (
                f"Domain DNS ({node.domain})",
                _collect(f"dig +short {q_domain} @8.8.8.8 2>/dev/null", fallback="dig not available"),
            )
        )

    # --- Output ---
    err_console.print()
    line()
    err_console.print()
    err_console.print("  [bold]Diagnostics collected.[/bold]\n")

    diag_text = _format_sections(sections)

    if ai:
        from meridian.ai import build_ai_prompt

        build_ai_prompt("diagnostics", diag_text, __version__)
    else:
        err_console.print(
            "  [bold]Tip:[/bold] [info]meridian doctor --ai[/info] -- paste directly into ChatGPT or Claude for help"
        )
        err_console.print()
        err_console.print("  1. Review the output below for any private info you want to remove")
        err_console.print("  2. Copy the markdown block into a new issue:")
        err_console.print("     [info]https://github.com/uburuntu/meridian/issues/new[/info]")
        err_console.print()
        line()
        err_console.print()
        err_console.print(diag_text)
        err_console.print()
        line()
        err_console.print()
        err_console.print("  [dim]Secrets (UUIDs, passwords, keys) are auto-redacted.[/dim]\n")


def _check_cert_expiry(conn: ServerConnection) -> str:
    """Check TLS certificate expiry on the local nginx."""
    from datetime import datetime, timezone

    result = conn.run(
        "echo | openssl s_client -connect 127.0.0.1:8443 -servername localhost 2>/dev/null "
        "| openssl x509 -noout -enddate 2>/dev/null",
        timeout=10,
    )
    raw = result.stdout.strip()
    if not raw or "notAfter" not in raw:
        return "could not check (nginx may not be running)"

    # Parse "notAfter=Apr  7 12:00:00 2026 GMT"
    date_str = raw.split("=", 1)[1].strip()
    try:
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = (expiry - now).days
        expiry_fmt = expiry.strftime("%Y-%m-%d")
        if days_left < 0:
            return f"EXPIRED ({expiry_fmt}) — run: meridian deploy to renew"
        elif days_left < 7:
            return f"expires in {days_left} days ({expiry_fmt}) — renewal needed soon"
        else:
            return f"valid until {expiry_fmt} ({days_left} days)"
    except (ValueError, IndexError):
        return f"raw: {date_str}"


def _redact_secrets(text: str) -> str:
    """Redact UUIDs, passwords, and keys from text."""
    # Redact UUIDs
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "[UUID-REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Redact passwords/keys/secrets
    text = re.sub(
        r"([Pp]assword|[Kk]ey|[Ss]ecret)[=: ]*[^ ]*",
        r"\1=[REDACTED]",
        text,
    )
    return text


def _format_sections(sections: list[tuple[str, str]]) -> str:
    """Format diagnostic sections as markdown."""
    parts: list[str] = []
    for title, body in sections:
        parts.append(f"### {title}\n```\n{body}\n```")
    return "\n\n".join(parts)
