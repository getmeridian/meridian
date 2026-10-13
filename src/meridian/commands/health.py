"""`meridian check`: role-aware server health on the shared verification contract.

A server that is not in the fleet yet gets pre-flight checks; a control, exit,
or gateway server gets container, listener, TLS, and disk checks; a relay
server gets its Realm hops checked. With no target, every fleet server is checked.
"""

from __future__ import annotations

import ipaddress
import time
from contextlib import ExitStack

import typer

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import load_cluster, make_panel
from meridian.commands.check_render import emit_verification_json, render_verification
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import DEFAULT_SNI
from meridian.console import err_console, error_context, fail, info, is_json_mode
from meridian.core.errors import LocalStateError
from meridian.core.fleet import TopologyNode
from meridian.core.health import HealthCheckResult, HealthReport, HealthScope, combine_health_reports, health_report
from meridian.core.inputs import validate_hostname_value
from meridian.core.output import OperationContext
from meridian.core.topology import SetupIntent
from meridian.core.verification import VerificationCheck, VerificationCounts, VerificationVerdict
from meridian.diagnostics.blocking import blocking_jobs
from meridian.diagnostics.checks import (
    check_container_running,
    check_disk_space,
    check_port_listening,
    check_tls_certificate,
)
from meridian.diagnostics.evidence import check_from_result, finding, finish_check
from meridian.diagnostics.network import resolve_hostname
from meridian.diagnostics.preflight import collect_preflight
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.resolve import connection_for_entry, local_entry, merge_target
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import SSHError
from meridian.ssh_ui import RichSSHUI
from meridian.verification import deployment_verification_context

_TITLES = {"preflight": "Pre-flight check", "server": "Server health", "relay": "Relay hops"}
_PANEL_CONTAINERS = ("remnawave", "remnawave-db", "remnawave-redis")


def run(
    target: str = "",
    *,
    user: str = "",
    ssh_port: int = 0,
    sni: str = "",
    domain: str = "",
    ai: bool = False,
    scope: HealthScope | None = None,
) -> None:
    """Check one server (or the whole fleet) and exit 0 / 4 findings / 3 inconclusive."""
    try:
        sni = validate_hostname_value(sni) if sni else ""
        domain = validate_hostname_value(domain) if domain else ""
    except ValueError as exc:
        with error_context("check"):
            fail(str(exc), hint_type="user")
    from meridian.config import SERVER_PROFILES_FILE

    operation = OperationContext()
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    with error_context("check", timer=operation.timer):
        cluster = ClusterConfig() if scope == "preflight" else load_cluster()
        reports = _collect(cluster, registry, target, user=user, port=ssh_port, sni=sni, domain=domain, scope=scope)
    result = combine_health_reports(reports)
    summary = _summary("Health check" if len(reports) > 1 else _TITLES[reports[0].scope], result.verdict, result.counts)
    if is_json_mode():
        emit_verification_json(
            command="check",
            data=result.to_data(),
            verdict=result.verdict,
            counts=result.counts,
            exit_code=result.exit_code,
            summary=summary,
            timer=operation.timer,
            inconclusive_code="MERIDIAN_CHECK_INCONCLUSIVE",
            inconclusive_hint="Restore SSH or panel access to every checked server, then rerun the check.",
        )
    else:
        _render(result, summary)
    if ai:
        _copy_ai_prompt(result)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


def _collect(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    target: str,
    *,
    user: str,
    port: int,
    sni: str,
    domain: str,
    scope: HealthScope | None,
) -> list[HealthReport]:
    intent = cluster.topology_intent
    if scope == "preflight" or intent is None:
        return [_preflight_report(registry, target, user=user, port=port, sni=sni, domain=domain)]
    if target:
        entry = _saved_entry(registry, target, user=user, port=port)
        roles = server_roles(intent, entry.id) if entry is not None else []
        if entry is None or not roles:
            if scope == "server":
                fail(
                    f"{target} is not a server in the fleet",
                    hint="See the fleet: meridian fleet inventory",
                    hint_type="user",
                )
            return [_preflight_report(registry, target, user=user, port=port, sni=sni, domain=domain)]
        servers = [(entry, roles)]
    else:
        servers = []
        for ref in fleet_server_refs(intent):
            entry = registry.find(ref)
            if entry is None:
                fail(
                    f"The fleet uses saved server {ref!r}, which is missing",
                    hint="Add it back: meridian server add",
                    hint_type="user",
                )
            servers.append((entry, server_roles(intent, ref)))

    nodes = {node.ip: node for node in topology_from_local_cluster(cluster).nodes}
    reports: list[HealthReport] = []
    with ExitStack() as stack:
        panel = _PanelSession(cluster, stack)
        for entry, roles in servers:
            if scope in (None, "server") and set(roles) - {"relay"}:
                _announce(entry)
                reports.append(
                    _server_report(cluster, registry, entry, roles, nodes.get(entry.host), panel, user=user, port=port)
                )
            if scope is None and "relay" in roles:
                reports.append(_relay_report(entry, roles, user=user))
    return reports


def server_roles(intent: SetupIntent, server_ref: str) -> list[str]:
    """Fleet roles of one saved server, in a stable order."""
    roles = []
    if intent.control.server_ref == server_ref:
        roles.append("control")
    if any(exit_.server_ref == server_ref for exit_ in intent.exits):
        roles.append("exit")
    if any(gateway.server_ref == server_ref for gateway in intent.routing_gateways):
        roles.append("gateway")
    if any(server_ref in relay.hop_server_refs for relay in intent.transparent_relays):
        roles.append("relay")
    return roles


def fleet_server_refs(intent: SetupIntent) -> list[str]:
    refs = [
        intent.control.server_ref,
        *(exit_.server_ref for exit_ in intent.exits),
        *(gateway.server_ref for gateway in intent.routing_gateways),
        *(ref for relay in intent.transparent_relays for ref in relay.hop_server_refs),
    ]
    return list(dict.fromkeys(refs))


def _saved_entry(registry: ServerRegistry, target: str, *, user: str, port: int) -> ServerEntry | None:
    try:
        spec = merge_target(target, user=user, port=port)
    except ValueError as exc:
        fail(str(exc), hint="Use IP, saved name, user@host[:port], or 'local'.", hint_type="user")
    return local_entry(registry) if spec.local else registry.find(spec.host)


def _announce(entry: ServerEntry) -> None:
    if not is_json_mode():
        info(f"Checking {entry.name or entry.host}" + (f" ({entry.host})" if entry.name != entry.host else ""))


def _preflight_report(
    registry: ServerRegistry, target: str, *, user: str, port: int, sni: str, domain: str
) -> HealthReport:
    resolved = resolve_server(registry, explicit_ip=target, user=user, port=port)
    try:
        probe_ip = str(ipaddress.ip_address(resolved.ip))
    except ValueError:
        probe_ip = resolve_hostname(resolved.ip, timeout=5) or ""
        if not probe_ip:
            fail(
                f"Could not resolve server hostname '{resolved.ip}'",
                hint="Check local DNS or pass the server's public IP explicitly.",
                hint_type="system",
            )
    resolved = ensure_server_connection(resolved)
    note = None if is_json_mode() else (lambda label: info(f"Checking {label}..."))
    checks = collect_preflight(
        resolved.conn,
        host=resolved.ip,
        probe_ip=probe_ip,
        sni=sni or DEFAULT_SNI,
        domain=domain,
        local_vantage=resolved.local_mode,
        progress=note,
    )
    entry = registry.find(resolved.ip)
    return health_report(resolved.ip, "preflight", checks, server_ref=entry.id if entry else "")


class _PanelSession:
    """One lazily opened panel client shared by every server; unreachable means skipped evidence."""

    def __init__(self, cluster: ClusterConfig, stack: ExitStack) -> None:
        self._cluster = cluster
        self._stack = stack
        self._panel: MeridianPanel | None = None
        self.error = ""

    def node_check(self, node: TopologyNode) -> VerificationCheck:
        started = time.monotonic()
        try:
            if self._panel is None and not self.error:
                self._panel = self._stack.enter_context(make_panel(self._cluster))
            api_node = self._panel.get_node(node.uuid) if self._panel is not None and node.uuid else None
        except RemnawaveError as exc:
            self.error = str(exc) or "panel unreachable"
        if self.error:
            found = finding("panel_node", "skipped", f"Panel unreachable: {self.error}")
        elif api_node is None:
            found = finding("panel_node", "failed", "Node is not registered in the panel", "Re-run: meridian apply")
        elif api_node.is_disabled:
            found = finding("panel_node", "warning", "Node is disabled in the panel", "Enable it in the panel")
        elif api_node.is_connected:
            found = finding("panel_node", "passed", "Node is connected to the panel")
        else:
            found = finding(
                "panel_node",
                "failed",
                "Node is registered but disconnected",
                f"Check the node container: meridian doctor {node.ip}",
            )
        return finish_check("panel_node", "Panel connection", [found], started)


def _server_report(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    entry: ServerEntry,
    roles: list[str],
    node: TopologyNode | None,
    panel: _PanelSession,
    *,
    user: str,
    port: int,
) -> HealthReport:
    label = entry.name or entry.host
    checks: list[VerificationCheck] = []
    if node is not None:
        checks.append(panel.node_check(node))

    conn = connection_for_entry(entry, user=user, port=port)
    started = time.monotonic()
    try:
        conn.check_ssh(ui=None if is_json_mode() else RichSSHUI())
    except (OSError, SSHError) as exc:
        hint = getattr(exc, "hint", "") or "Check the saved SSH user and port: meridian server list"
        checks.append(finish_check("ssh", "SSH", [finding("ssh", "skipped", f"Cannot connect: {exc}", hint)], started))
        return health_report(label, "server", checks, server_ref=entry.id, roles=roles)
    message = "Running on this server" if conn.local_mode else f"Connected as {conn.user}"
    checks.append(finish_check("ssh", "SSH", [finding("ssh", "passed", message)], started))

    containers: list[str] = []
    if {"exit", "gateway"} & set(roles):
        containers.append("remnawave-node")
    if "control" in roles:
        containers.extend(_PANEL_CONTAINERS)
        if cluster.subscription_page is not None and cluster.subscription_page.deployed:
            containers.append("remnawave-subscription-page")
    for name in containers:
        checks.append(check_from_result(f"container:{name}", f"Container {name}", check_container_running(conn, name)))

    try:
        deployment = deployment_verification_context(cluster, registry, entry.host)
    except (LocalStateError, ValueError) as exc:
        skipped = finding("listeners", "skipped", f"Listener evidence is unavailable: {exc}")
        checks.append(finish_check("listeners", "Listeners", [skipped], time.monotonic()))
    else:
        for port_number in deployment.public_tcp_ports:
            result = check_port_listening(conn, port_number, transport="tcp")
            checks.append(check_from_result(f"tcp:{port_number}", f"Public TCP {port_number}", result))
        for port_number in deployment.public_udp_ports:
            result = check_port_listening(conn, port_number, transport="udp")
            checks.append(check_from_result(f"udp:{port_number}", f"Public UDP {port_number}", result))
        for purpose, port_number in deployment.internal_ports.items():
            result = check_port_listening(conn, port_number, transport="any")
            checks.append(check_from_result(f"internal:{port_number}", f"Internal {port_number} ({purpose})", result))
        default_host = (node.domain or node.sni) if node is not None else ""
        for port_number in deployment.public_tls_ports:
            route = next((item for item in deployment.https_routes if item.port == port_number), None)
            host = (route.tls_sni or route.host_header) if route is not None else default_host
            result = check_tls_certificate(conn, host or entry.host, port=port_number)
            checks.append(check_from_result(f"tls:{port_number}", f"TLS certificate on {port_number}", result))
        if not conn.local_mode:
            # Deployer-vantage throughput: only meaningful (and only run) from another machine.
            checks.extend(job() for job in blocking_jobs(entry.host, deployment, differential=False))

    checks.append(check_from_result("disk", "Disk", check_disk_space(conn, min_free_mb=1024)))
    return health_report(label, "server", checks, server_ref=entry.id, roles=roles)


def _relay_report(entry: ServerEntry, roles: list[str], *, user: str) -> HealthReport:
    from meridian.commands.relay_check import collect_relay_check

    relay = collect_relay_check(entry.host, "", user, connection_factory=None)
    return health_report(entry.name or entry.host, "relay", relay.checks, server_ref=entry.id, roles=roles)


def _summary(subject: str, verdict: VerificationVerdict, counts: VerificationCounts) -> str:
    if verdict == "passed":
        return f"{subject}: all {counts.checks} checks passed."
    if verdict == "findings":
        return f"{subject}: {counts.failed + counts.warnings} of {counts.checks} checks found problems."
    return f"{subject}: {counts.skipped} of {counts.checks} checks could not collect evidence."


def _render(result: HealthCheckResult, summary: str) -> None:
    for report in result.reports:
        roles = f" - {', '.join(report.roles)}" if report.roles else ""
        render_verification(
            _TITLES[report.scope],
            f"{report.target}{roles}",
            report.checks,
            report.verdict,
            _summary(_TITLES[report.scope], report.verdict, report.counts),
        )
    if len(result.reports) > 1:
        style = {"passed": "green", "findings": "red", "inconclusive": "yellow"}[result.verdict]
        err_console.print(f"  [{style}][bold]{summary}[/bold][/{style}]\n")
    elif result.reports[0].scope == "preflight" and result.verdict == "passed":
        target = result.reports[0].target
        err_console.print(f"  [dim]Next: meridian deploy {target}[/dim]")
        err_console.print(f"  [dim]Best camouflage target: meridian scan {target}[/dim]\n")


def _copy_ai_prompt(result: HealthCheckResult) -> None:
    from meridian import __version__
    from meridian.ai import build_ai_prompt

    lines: list[str] = []
    for report in result.reports:
        lines.append(f"{_TITLES[report.scope]} for {report.target} ({', '.join(report.roles) or 'not deployed'})")
        for check in report.checks:
            lines.extend(f"  [{finding.status}] {check.name}: {finding.message}" for finding in check.findings)
    lines.append(f"Verdict: {result.verdict}")
    build_ai_prompt("check", "\n".join(lines), __version__)
