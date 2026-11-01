"""`meridian check`: role-aware server health on the shared verification contract.

A server that is not in the fleet yet gets pre-flight checks; a control, exit,
or gateway server gets unit, agent, listener, TLS, and disk checks; a relay
server gets its Realm hops checked. With no target, every fleet server is
checked.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import TypeVar

import typer

from meridian import __version__
from meridian.adapters.cluster import topology_from_local_cluster
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import load_cluster
from meridian.commands.check_render import emit_verification_json, render_verification
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import DEFAULT_SNI
from meridian.console import err_console, error_context, fail, info, is_json_mode
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control_artifacts import expected_agent_version
from meridian.core.errors import LocalStateError
from meridian.core.execution import ServerConnection
from meridian.core.fleet import TopologyNode
from meridian.core.health import HealthCheckResult, HealthReport, combine_health_reports, health_report
from meridian.core.inputs import validate_hostname_value
from meridian.core.output import OperationContext
from meridian.core.topology import SetupIntent
from meridian.core.verification import VerificationCheck, VerificationCounts, VerificationVerdict
from meridian.diagnostics.blocking import blocking_jobs
from meridian.diagnostics.checks import (
    check_disk_space,
    check_port_listening,
    check_tls_certificate,
)
from meridian.diagnostics.control_service import (
    agent_findings,
    agent_version_finding,
    background_findings,
    check_unit_active,
    service_version_finding,
)
from meridian.diagnostics.evidence import check_from_result, finding, finish_check
from meridian.diagnostics.exit_dns import check_exit_dns
from meridian.diagnostics.network import resolve_hostname
from meridian.diagnostics.preflight import collect_preflight
from meridian.host_runtime import (
    AGENT_UNIT,
    CONTROL_SERVICE_UNIT,
    CORE_UNIT,
)
from meridian.resolve import connection_for_entry, local_entry, merge_target
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import SSHError
from meridian.ssh_ui import RichSSHUI
from meridian.verification import deployment_verification_context

T = TypeVar("T")

_TITLES = {"preflight": "Pre-flight check", "server": "Server health", "relay": "Relay hops"}


def run(
    target: str = "",
    *,
    user: str = "",
    ssh_port: int = 0,
    sni: str = "",
    domain: str = "",
    ai: bool = False,
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
        cluster = load_cluster()
        reports = _collect(cluster, registry, target, user=user, port=ssh_port, sni=sni, domain=domain)
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
) -> list[HealthReport]:
    intent = cluster.topology_intent
    if intent is None:
        return [_preflight_report(registry, target, user=user, port=port, sni=sni, domain=domain)]
    if target:
        entry = _saved_entry(registry, target, user=user, port=port)
        roles = server_roles(intent, entry.id) if entry is not None else []
        if entry is None or not roles:
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
        panel = _ServiceSession(cluster, stack)
        for entry, roles in servers:
            if set(roles) - {"relay"}:
                _announce(entry)
                reports.append(
                    _server_report(cluster, registry, entry, roles, nodes.get(entry.host), panel, user=user, port=port)
                )
            if "relay" in roles:
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


class _ServiceSession:
    """One lazily opened control-service client; unreachable means skipped evidence."""

    def __init__(self, cluster: ClusterConfig, stack: ExitStack) -> None:
        self._cluster = cluster
        self._stack = stack
        self._client: ControlClient | None = None
        self.error = ""

    def service_check(self) -> VerificationCheck:
        started = time.monotonic()
        health = self._read(lambda client: client.health())
        if health is None:
            found = finding("control_service", "skipped", f"Control service unreachable: {self.error}")
        elif self._cluster.fleet_id and health.fleet_id != self._cluster.fleet_id:
            found = finding(
                "control_service",
                "failed",
                "The control service belongs to another fleet",
                "Restore this fleet's local state, or redeploy the control server",
            )
        else:
            found = finding("control_service", "passed", f"Control service {health.version} answers")
            version = service_version_finding(__version__, health.version)
            return finish_check(
                "control_service", "Control service", [found, version, *background_findings(health)], started
            )
        return finish_check("control_service", "Control service", [found], started)

    def node_check(self, node: TopologyNode) -> VerificationCheck:
        started = time.monotonic()
        status = self._read(lambda client: client.node(node.name))
        if self.error:
            findings = [finding("agent", "skipped", f"Control service unreachable: {self.error}")]
        else:
            findings = agent_findings(status, logs_hint=f"meridian logs {node.ip} --component node")
            expected = expected_agent_version(status.agent_version if status is not None else None)
            version = agent_version_finding(expected, status) if status and status.runtime_available() else None
            if version is not None:
                findings.append(version)
        return finish_check("agent", "Node agent", findings, started)

    def _read(self, call: Callable[[ControlClient], T]) -> T | None:
        if self.error:
            return None
        try:
            if self._client is None:
                panel = self._cluster.panel
                self._client = self._stack.enter_context(ControlClient(panel.url, panel.api_token, timeout=20))
            return call(self._client)
        except ControlServiceError as exc:
            self.error = str(exc) or "no answer"
            return None


def _server_report(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    entry: ServerEntry,
    roles: list[str],
    node: TopologyNode | None,
    panel: _ServiceSession,
    *,
    user: str,
    port: int,
) -> HealthReport:
    label = entry.name or entry.host
    checks: list[VerificationCheck] = []
    if "control" in roles:
        checks.append(panel.service_check())
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

    checks.extend(_service_unit_checks(conn, roles, entry.host))
    if "exit" in roles:
        checks.append(check_exit_dns(conn))

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
            # Every name Meridian certifies on the port: a panel domain and the server's IP share 443, and
            # checking only the first missed a swapped certificate (#110). Without one, the camouflage site's.
            routes = [item for item in deployment.https_routes if item.port == port_number]
            managed = [item for item in routes if item.kind != "camouflage"]
            camouflage = not managed and bool(routes)
            hosts = sorted({item.tls_sni or item.host_header or entry.host for item in managed or routes[:1]})
            for host in hosts or [default_host or entry.host]:
                result = check_tls_certificate(conn, host, port=port_number)
                tls_label = "Camouflage site TLS" if camouflage else "TLS certificate"
                check_id = f"tls:{port_number}" if len(hosts) <= 1 else f"tls:{port_number}:{host}"
                check_name = f"{tls_label} on {port_number}" + (f" ({host})" if len(hosts) > 1 else "")
                checks.append(check_from_result(check_id, check_name, result))
        if not conn.local_mode:
            # Deployer-vantage throughput: only meaningful (and only run) from another machine.
            checks.extend(job() for job in blocking_jobs(entry.host, deployment, differential=False))

    checks.append(check_from_result("disk", "Disk", check_disk_space(conn, min_free_mb=1024)))
    return health_report(label, "server", checks, server_ref=entry.id, roles=roles)


def _service_unit_checks(conn: ServerConnection, roles: list[str], host: str) -> list[VerificationCheck]:
    """The control service, agent, and core units each role runs."""
    logs = f"meridian logs {host} --component"
    units: list[tuple[str, str, str]] = []
    if "control" in roles:
        units.append(
            (CONTROL_SERVICE_UNIT, "Control service unit", f"Read its log ({logs} panel), then: meridian apply")
        )
    if {"exit", "gateway"} & set(roles):
        units.append((AGENT_UNIT, "Node agent unit", f"See {logs} node; then: meridian apply"))
        # The agent owns the core and repairs it on its next reconcile pass.
        units.append(
            (CORE_UNIT, "Proxy core unit", f"The node agent restarts it within 5 minutes; if not: {logs} node")
        )
    return [
        check_from_result(f"unit:{unit}", label, check_unit_active(conn, unit, remediation=fix))
        for unit, label, fix in units
    ]


def _relay_report(entry: ServerEntry, roles: list[str], *, user: str) -> HealthReport:
    from meridian.commands.relay_check import collect_relay_check

    relay = collect_relay_check(entry.host, user)
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
