"""System diagnostics collection for bug reports."""

from __future__ import annotations

import platform
import re
import shlex

import typer

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.commands._helpers import load_cluster
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
from meridian.console import err_console, fail, line
from meridian.core.errors import LocalStateError
from meridian.core.execution import ServerConnection as ServerConnectionLike
from meridian.core.inputs import validate_hostname_value
from meridian.core.redaction import REDACTED, redact_string
from meridian.diagnostics import command_evidence_unavailable
from meridian.facts import ServerFacts
from meridian.servers import ServerRegistry
from meridian.ssh import CommandResult, ServerConnection, SSHError
from meridian.verification import DeploymentVerificationContext, deployment_verification_context


class _EvidenceConnection:
    """Convert lost remote execution into explicit partial diagnostic evidence."""

    def __init__(self, connection: ServerConnection) -> None:
        self.connection = connection
        self.unavailable = False

    def run(
        self,
        command: str,
        timeout: int = 30,
        *,
        sudo: bool | None = None,
        sensitive: bool = False,
    ) -> CommandResult:
        try:
            result = self.connection.run(
                command,
                timeout=timeout,
                sudo=sudo,
                sensitive=sensitive,
            )
        except (OSError, SSHError) as exc:
            self.unavailable = True
            return CommandResult(args=command, returncode=255, stderr=str(exc))
        if command_evidence_unavailable(result.returncode) or getattr(result, "timed_out", False):
            self.unavailable = True
        return result


def run(
    ip: str = "",
    sni: str = "",
    user: str = "root",
    ai: bool = False,
    requested_server: str = "",
) -> None:
    """Collect system info from the server for bug reports. Redacts secrets."""
    if sni:
        try:
            sni = validate_hostname_value(sni)
        except ValueError as exc:
            fail(str(exc), hint_type="user")
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    resolved = ensure_server_connection(resolved)
    evidence_conn = _EvidenceConnection(resolved.conn)

    from meridian import __version__

    err_console.print()
    err_console.print("  [bold]Meridian Diagnostics[/bold]")
    err_console.print("  [dim]Collecting system info for bug reports...[/dim]")
    err_console.print("  [warn]Note: secrets (passwords, UUIDs, keys) are redacted.[/warn]")
    err_console.print()

    sections: list[tuple[str, str]] = []

    def _collect(cmd: str, timeout: int = 10, fallback: str = "unknown") -> str:
        """Run a remote command, return output with the command as a comment header."""
        output = evidence_conn.run(cmd, timeout=timeout).stdout.strip() or fallback
        return f"$ {cmd}\n{output}"

    # --- Local Machine ---
    local_os = platform.platform()

    # Check deployed version
    cluster = load_cluster(require_configured=False)
    legacy_node = cluster.find_node(resolved.ip)
    deployed_with = (
        (legacy_node.deployed_with if legacy_node else "")
        or (cluster.panel.deployed_with if cluster.panel.server_ip == resolved.ip else "")
        or "unknown"
    )

    local_info = f"OS: {local_os}\nMeridian: {__version__}\nServer deployed with: {deployed_with}"

    sections.append(
        (
            "Local Machine",
            local_info,
        )
    )

    # --- Deployment context (for AI enrichment) ---
    domain = (legacy_node.domain if legacy_node else "") or ""
    saved_sni = (legacy_node.sni if legacy_node else "") or ""
    roles: set[str] = set()
    protocols: set[str] = set()
    relay_count = len(cluster.relays)
    relay_ports: set[int] = set()
    xray_expected = legacy_node is not None
    topology_error = ""
    deployment: DeploymentVerificationContext | None = None

    try:
        topology = topology_from_local_cluster(cluster)
        topology_node = topology.find_node(resolved.ip)
        topology_relay = next((relay for relay in topology.relays if relay.ip == resolved.ip), None)
        if topology.panel.server_ip == resolved.ip:
            roles.add("panel")
        if topology_node is not None:
            roles.add("routing-gateway" if topology_node.role == "routing_gateway" else "exit")
            domain = topology_node.domain or domain
            saved_sni = topology_node.sni or saved_sni
            protocols.update(topology_node.protocols)
            xray_expected = True
        if topology_relay is not None:
            roles.add("relay")
            saved_sni = topology_relay.sni or saved_sni
            relay_ports.add(topology_relay.port)
        relay_count = len(topology.relays)
    except LocalStateError as exc:
        topology_error = str(exc)

    # V4 intent identifies internal relay hops that are not public endpoints.
    intent = cluster.topology_intent
    try:
        server = registry.find(resolved.ip)
    except LocalStateError as exc:
        server = None
        topology_error = f"{topology_error}; {exc}" if topology_error else str(exc)
    if intent is not None and server is not None:
        try:
            deployment = deployment_verification_context(cluster, registry, resolved.ip)
        except (LocalStateError, ValueError) as exc:
            topology_error = f"{topology_error}; {exc}" if topology_error else str(exc)
        else:
            server_ref = server.id
            if intent.control.server_ref == server_ref:
                roles.add("panel")
            matching_exits = [exit_ for exit_ in intent.exits if exit_.server_ref == server_ref]
            matching_gateways = [gateway for gateway in intent.routing_gateways if gateway.server_ref == server_ref]
            matching_relays = [relay for relay in intent.transparent_relays if server_ref in relay.hop_server_refs]
            if matching_exits:
                roles.add("exit")
            if matching_relays:
                roles.add("relay")
            if matching_gateways:
                roles.add("routing-gateway")
            xray_expected = bool(matching_exits or matching_gateways)
            relay_count = len(intent.transparent_relays)
            for exit_ in matching_exits:
                protocols.update(path.protocol for path in exit_.paths)
                reality_path = next((path for path in exit_.paths if path.protocol == "reality"), None)
                tls_path = next((path for path in exit_.paths if path.tls_sni), None)
                if reality_path is not None:
                    saved_sni = reality_path.reality_sni or saved_sni
                if tls_path is not None:
                    domain = tls_path.tls_sni or domain
            paths_by_id = {path.id: path for exit_ in intent.exits for path in exit_.paths}
            for gateway in matching_gateways:
                if bridge_path := paths_by_id.get(gateway.bridge_path_ref):
                    protocols.add(bridge_path.protocol)
                    saved_sni = bridge_path.reality_sni or saved_sni
                    domain = bridge_path.tls_sni or domain
            for relay in matching_relays:
                saved_sni = relay.reality_sni or saved_sni
                relay_ports.add(relay.listen_port)

    mode = "domain" if domain else "IP"
    if relay_count:
        mode += f" + {relay_count} relay(s)"

    deployment_info = (
        f"Mode: {mode}\n"
        f"Roles: {', '.join(sorted(roles)) if roles else 'untracked'}\n"
        f"Protocols: {', '.join(sorted(protocols)) if protocols else 'unknown'}"
    )
    sections.append(("Deployment", deployment_info))
    if topology_error:
        sections.append(("Topology Evidence", f"unavailable: {topology_error}"))

    facts = ServerFacts(evidence_conn)
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
    if not xray_expected:
        xray_status = "not expected for this server's configured roles"
    else:
        xray_pid_raw = evidence_conn.run(
            "docker exec remnawave-node pgrep -f xray 2>/dev/null",
            timeout=10,
        ).stdout.strip()
        if xray_pid_raw:
            xray_status = f"running (PID {xray_pid_raw.splitlines()[0]})"
        else:
            xray_status = "NOT RUNNING - proxy traffic is not flowing"
    sections.append(("Xray Process", xray_status))

    # --- Node capabilities ---
    from meridian.capabilities import detect_capabilities

    cap_lines = ["not applicable for this server's configured roles"]
    if xray_expected:
        caps = detect_capabilities(evidence_conn)
        cap_lines = [f"Xray version: {caps.xray_version or 'unknown'}"]
        enabled = [k for k in ("reality", "xhttp", "wss", "hysteria2", "finalmask") if getattr(caps, k)]
        cap_lines.append(f"Enabled: {', '.join(enabled) if enabled else 'none detected'}")
    sections.append(("Node Capabilities", "\n".join(cap_lines)))

    # --- Role-specific runtime evidence ---
    if xray_expected:
        log_cmd = "docker logs remnawave-node --tail 50 2>&1 | grep -v '^\\s*$' | sort -u | tail -20"
        xray_logs = evidence_conn.run(log_cmd, timeout=15).stdout.strip() or "container not running"
        xray_logs = _redact_secrets(xray_logs)
        sections.append(("Remnawave Node Logs", f"$ {log_cmd}\n{xray_logs}"))

    nginx_expected = bool(roles.intersection({"panel", "exit", "routing-gateway"}))
    if nginx_expected:
        nginx_cmd = "tail -10 /var/log/nginx/error.log 2>/dev/null"
        nginx_errors = evidence_conn.run(nginx_cmd, timeout=10).stdout.strip() or "no errors or log not found"
        nginx_errors = _redact_secrets(nginx_errors)
        sections.append(("Nginx Errors", f"$ {nginx_cmd}\n{nginx_errors}"))
        cert_port = 8443
        cert_sni = "localhost"
        if deployment is not None and deployment.kind == "v4":
            route = next(
                (candidate for candidate in deployment.https_routes if candidate.kind == "managed"),
                deployment.https_routes[0] if deployment.https_routes else None,
            )
            if route is not None:
                cert_port = route.port
                cert_sni = route.tls_sni or route.host_header or cert_sni
        cert_expiry = _check_cert_expiry(evidence_conn, port=cert_port, server_name=cert_sni)
        sections.append(("TLS Certificate", cert_expiry))

    # --- Listening Ports ---
    listener_ports = set(relay_ports)
    socket_flags = "-tlnp"
    if deployment is not None and deployment.kind == "v4":
        listener_ports.update(deployment.public_tcp_ports)
        listener_ports.update(deployment.public_udp_ports)
        listener_ports.update(deployment.internal_ports.values())
        socket_flags = "-tulnp"
    elif nginx_expected or xray_expected:
        listener_ports.update({80, 443, 8443, 8444})
    port_filter = " or ".join(f"sport = :{shlex.quote(str(port))}" for port in sorted(listener_ports))
    q_socket_flags = shlex.quote(socket_flags)
    listener_command = f"ss {q_socket_flags} {port_filter} 2>&1" if port_filter else f"ss {q_socket_flags} 2>&1"
    sections.append(("Listening Ports", _collect(listener_command)))

    # --- Firewall ---
    sections.append(("Firewall (UFW)", _collect("ufw status verbose 2>&1", fallback="ufw not available")))

    # --- Geo-blocking ---
    if xray_expected:
        geo_status = "managed by Remnawave (check panel settings)"
        sections.append(("Geo-blocking", geo_status))

    # --- SNI Target ---
    if xray_expected:
        sni_host = sni or saved_sni or DEFAULT_SNI
        q_sni = shlex.quote(sni_host)
        sni_cmd = (
            f"echo | openssl s_client -connect {q_sni}:443 -servername {q_sni} 2>/dev/null "
            f"| grep -E 'subject=|issuer=|CONNECTED'"
        )
        sni_check = evidence_conn.run(sni_cmd, timeout=10).stdout.strip() or "unreachable"
        sections.append((f"Camouflage Target ({sni_host})", f"$ {sni_cmd}\n{sni_check}"))

    # --- Domain DNS ---
    if domain:
        q_domain = shlex.quote(domain)
        sections.append(
            (
                f"Domain DNS ({domain})",
                _collect(
                    f"getent ahosts {q_domain} 2>/dev/null | awk '{{print $1}}' | sort -u",
                    fallback="system resolver returned no addresses",
                ),
            )
        )

    # --- Output ---
    err_console.print()
    line()
    err_console.print()
    err_console.print("  [bold]Diagnostics collected.[/bold]\n")

    diag_text = _redact_secrets(_format_sections(sections))

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
        err_console.print("     [info]https://github.com/getmeridian/meridian/issues/new[/info]")
        err_console.print()
        line()
        err_console.print()
        err_console.print(diag_text, markup=False)
        err_console.print()
        line()
        err_console.print()
        err_console.print("  [dim]Secrets (UUIDs, passwords, keys) are auto-redacted.[/dim]\n")
    if evidence_conn.unavailable or topology_error:
        from meridian.console import warn

        warn("Diagnostics are partial because required remote evidence was unavailable")
        raise typer.Exit(3)


def _check_cert_expiry(
    conn: ServerConnectionLike,
    *,
    port: int = 8443,
    server_name: str = "localhost",
) -> str:
    """Check TLS certificate expiry on the local nginx."""
    from datetime import datetime, timezone

    q_server_name = shlex.quote(server_name)
    q_port = shlex.quote(str(port))
    result = conn.run(
        f"echo | openssl s_client -connect 127.0.0.1:{q_port} -servername {q_server_name} 2>/dev/null "
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
    """Apply the central free-form redactor plus diagnostics-only UUID masking."""
    text = redact_string(text).replace(REDACTED, "[REDACTED]")
    text = re.sub(
        r"(?P<key>\b(?:password|passwd|key|secret)\b)(?P<sep>\s*[:=]\s*)[^\s,;]+",
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Redact UUIDs
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "[UUID-REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _format_sections(sections: list[tuple[str, str]]) -> str:
    """Format diagnostic sections as markdown."""
    parts: list[str] = []
    for title, body in sections:
        parts.append(f"### {title}\n```\n{body}\n```")
    return "\n\n".join(parts)
