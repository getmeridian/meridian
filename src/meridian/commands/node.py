"""Node management -- add, list, check, remove proxy nodes in the fleet.

Nodes are servers running Remnawave node + Xray. All node state is
tracked in the panel database; cluster.yml stores topology for local
reference and SSH access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.commands._helpers import (
    format_traffic,
    load_topology_cluster,
    make_panel,
    persist_reviewed_apply,
    reviewed_apply_persistence,
)
from meridian.commands._validation import validate_command_input
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.command_inputs import NodeAddRequest, NodeTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.errors import MeridianError as MeridianException
from meridian.core.fleet import build_node_list_result
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json

if TYPE_CHECKING:
    from meridian.diagnostics import CheckResult

# -- Node Add --


def run_add(
    ip: str,
    name: str = "",
    user: str = "root",
    ssh_port: int = 22,
    sni: str = "",
    domain: str = "",
    harden: bool = True,
    yes: bool = False,
) -> None:
    """Provision and add a new node to the fleet."""
    request = validate_command_input(
        NodeAddRequest,
        "Invalid node add request",
        ip=ip,
        name=name,
        user=user,
        ssh_port=ssh_port,
        sni=sni,
        domain=domain,
        harden=harden,
        yes=yes,
    )
    from meridian.commands.resolve import (
        ensure_server_connection,
        resolve_server,
    )
    from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.editor import add_exit_to_intent
    from meridian.setup.runtime import SetupRuntime

    cluster = load_topology_cluster()
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    if not request.harden:
        fail(
            "Fleet servers always keep OS hardening enabled.",
            hint=("Use `meridian setup` to review topology-managed server choices."),
            hint_type="user",
        )
    if not request.yes and not confirm(f"Add {request.name or request.ip} as a V4 exit?"):
        raise typer.Exit(1)

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = ensure_server_connection(
        resolve_server(
            registry,
            explicit_ip=request.ip,
            user=request.user,
            port=request.ssh_port,
        )
    )
    try:
        registry.add(
            ServerEntry(
                host=resolved.ip,
                user=resolved.user,
                name=request.name or resolved.ip,
                port=request.ssh_port,
                auth_state="validated",
            )
        )
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save the V4 node server profile: {exc}",
            hint="Check ~/.meridian permissions and disk space, then retry.",
            hint_type="system",
        )
    try:
        entry = registry.find(resolved.ip)
    except LocalStateError as exc:
        fail(exc)
    if entry is None:
        raise RuntimeError("Validated server was not saved")
    updated = add_exit_to_intent(
        intent,
        server_ref=entry.id,
        title=request.name or f"exit-{len(intent.exits) + 1}",
        reality_sni=request.sni or DEFAULT_SNI,
        tls_hostname=request.domain,
    )
    try:
        with reviewed_apply_persistence("V4 exit apply"):
            result = SetupRuntime(
                registry,
                cluster_loader=lambda: cluster,
                persist=persist_reviewed_apply,
            ).apply_intent(updated)
    except MeridianException as exc:
        fail(exc)
    if not result.all_succeeded:
        failures = "; ".join(f"{item.action.resource.logical_id}: {item.error}" for item in result.failed)
        fail(
            "V4 exit did not converge.",
            hint=failures,
            hint_type="system",
        )
    ok(f"Exit {request.name or resolved.ip} {'added' if result.changed else 'already converged'}")


# -- Node Check --


def run_check(ip_or_name: str, user: str = "") -> None:
    """Check health of a node: panel status, SSH, containers, ports, TLS."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.diagnostics import (
        check_container_running,
        check_disk_space,
        check_port_listening,
        check_tls_certificate,
    )
    from meridian.servers import ServerRegistry
    from meridian.ssh import ServerConnection, SSHError
    from meridian.ssh_ui import RichSSHUI
    from meridian.verification import deployment_verification_context

    request = validate_command_input(NodeTargetRequest, "Invalid node check request", ip_or_name=ip_or_name, user=user)
    cluster = load_topology_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    node = next(
        (item for item in topology.nodes if request.ip_or_name in {item.ip, item.name}),
        None,
    )
    if node is None:
        fail(f"Node '{request.ip_or_name}' not found", hint="Check: meridian node list", hint_type="user")

    err_console.print()
    info(f"Checking node {node.ip} ({node.name or 'unnamed'})...")
    err_console.print()

    exit_code = 0
    deployment = None
    try:
        deployment = deployment_verification_context(
            cluster,
            ServerRegistry(SERVER_PROFILES_FILE),
            node.ip,
        )
    except LocalStateError as exc:
        warn(f"V4 listener evidence is unavailable: {exc}")
        exit_code = 3

    # 1. Panel heartbeat (command-specific — requires panel client)
    try:
        panel = make_panel(cluster)
        with panel:
            api_node = panel.get_node(node.uuid) if node.uuid else None
            if api_node and api_node.is_disabled:
                err_console.print("  [yellow]![/yellow] Panel: node disabled")
                exit_code = 4
            elif api_node and api_node.is_connected:
                ok("Panel: node connected")
            elif api_node:
                err_console.print("  [red]✗[/red] Panel: node disconnected")
                exit_code = 4
            else:
                err_console.print("  [red]✗[/red] Panel: node not registered")
                exit_code = 4
    except RemnawaveError:
        err_console.print("  [yellow]![/yellow] Panel: unreachable")
        exit_code = max(exit_code, 3)

    # 2. SSH connectivity (command-specific — requires SSH auth)
    ssh_user = request.user or node.ssh_user or "root"
    try:
        conn = ServerConnection(ip=node.ip, user=ssh_user, port=node.ssh_port)
        conn.check_ssh(ui=RichSSHUI())
        ok("SSH: connected")
    except (OSError, SSHError):
        err_console.print("  [yellow]![/yellow] SSH: cannot connect")
        warn("Cannot proceed with server-side checks")
        err_console.print()
        raise typer.Exit(max(exit_code, 3))

    # 3. Docker containers — via diagnostics
    container_names = ["remnawave-node"]
    if node.is_panel_host:
        container_names.extend(("remnawave", "remnawave-db", "remnawave-redis"))
    for name in container_names:
        exit_code = max(exit_code, _render_check(check_container_running(conn, name), f"Container: {name}"))

    # 4. Listener and TLS evidence apply to both exits and routing gateways.
    if deployment is not None and deployment.kind == "v4":
        for port in deployment.public_tcp_ports:
            result = check_port_listening(conn, port, transport="tcp")
            exit_code = max(exit_code, _render_check(result, f"Public TCP port {port}"))
        for port in deployment.public_udp_ports:
            result = check_port_listening(conn, port, transport="udp")
            exit_code = max(exit_code, _render_check(result, f"Public UDP port {port}"))
        for label, port in deployment.internal_ports.items():
            result = check_port_listening(conn, port, transport="any")
            exit_code = max(exit_code, _render_check(result, f"Internal port {port} ({label})"))

        default_host = node.domain or node.sni or node.ip
        for port in deployment.public_tls_ports:
            route = next((candidate for candidate in deployment.https_routes if candidate.port == port), None)
            host = (route.tls_sni or route.host_header) if route is not None else default_host
            result = check_tls_certificate(conn, host or default_host, port=port)
            exit_code = max(exit_code, _render_check(result, f"TLS cert on port {port}"))
    else:
        warn("V4 listener evidence is unavailable for this saved server")
        exit_code = max(exit_code, 3)

    # 6. Disk space — via diagnostics
    exit_code = max(exit_code, _render_check(check_disk_space(conn, min_free_mb=1024), "Disk"))

    err_console.print()
    if exit_code == 0:
        ok("All checks passed")
    elif exit_code == 3:
        warn("Checks were inconclusive — required evidence was unavailable")
    else:
        warn("Some checks failed — review above")
    err_console.print()
    if exit_code:
        raise typer.Exit(exit_code)


def _render_check(result: CheckResult, label: str) -> int:
    """Render one diagnostic and return its verification-style exit code."""
    if result.status == "passed":
        ok(f"{label}: {result.detail}")
        return 0
    if result.status == "skipped":
        warn(f"{label}: {result.detail}")
        return 3
    if result.status == "warning":
        warn(f"{label}: {result.detail}")
        if result.remediation:
            err_console.print(f"    [dim]Run: {escape(result.remediation)}[/dim]")
        return 4
    # failed
    err_console.print(f"  [red]✗[/red] {escape(label)}: {escape(result.detail)}")
    if result.remediation:
        err_console.print(f"    [dim]Run: {escape(result.remediation)}[/dim]")
    return 4


# -- Node List --


def run_list() -> None:
    """List all nodes with health status from the panel."""
    from meridian.console import error_context

    operation = OperationContext()
    with error_context("node.list", timer=operation.timer):
        _run_list(operation=operation)


def _run_list(*, operation: OperationContext) -> None:
    """Implementation for node list with command metadata attached."""
    from rich.box import ROUNDED
    from rich.table import Table

    from meridian.console import is_json_mode

    cluster = load_topology_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    api_nodes = []
    warnings: list[MeridianError] = []
    try:
        with make_panel(cluster) as panel:
            api_nodes = panel.list_nodes()
    except RemnawaveError as exc:
        warnings.append(
            MeridianError(
                code="MERIDIAN_NODE_STATUS_UNAVAILABLE",
                category="system",
                message="Configured nodes are available, but live panel status could not be collected.",
                hint=exc.hint or "Check panel connectivity and retry.",
                retryable=True,
                exit_code=3,
            )
        )
        if not is_json_mode():
            warn("Panel node status is unavailable; configured nodes are shown as unknown")

    # Index API nodes by UUID for quick lookup
    api_by_uuid = {n.uuid: n for n in api_nodes}

    if is_json_mode():
        result = build_node_list_result(topology.nodes, api_nodes)
        count = len(result.nodes)
        exit_code = 3 if warnings else 0
        emit_json(
            command_envelope(
                command="node.list",
                data=result.to_data(),
                summary=Summary(
                    text=f"{count} node(s) in cluster",
                    changed=False,
                    counts={"nodes": count},
                ),
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            )
        )
        if exit_code:
            raise typer.Exit(exit_code)
        return

    table = Table(
        title="Proxy Nodes",
        show_lines=False,
        pad_edge=False,
        box=ROUNDED,
        padding=(0, 2),
    )
    table.add_column("IP", style="bold cyan")
    table.add_column("Name", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Xray", style="dim")
    table.add_column("Traffic", style="dim")

    for node in topology.nodes:
        api_node = api_by_uuid.get(node.uuid)
        if api_node:
            if api_node.is_disabled:
                status_text = "[dim]disabled[/dim]"
            elif api_node.is_connected:
                status_text = "[green]connected[/green]"
            else:
                status_text = "[red]disconnected[/red]"
            xray = escape(api_node.xray_version or "-")
            traffic = format_traffic(api_node.traffic_used)
        else:
            status_text = "[dim]unknown[/dim]"
            xray = "-"
            traffic = "-"

        label = escape(node.name or node.ip)
        if node.is_panel_host:
            label += " [dim](panel)[/dim]"
        if node.role == "routing_gateway":
            label += " [dim](routing gateway)[/dim]"

        table.add_row(escape(node.ip), label, status_text, xray, traffic)

    err_console.print()
    err_console.print(table)
    n_cluster = len(topology.nodes)
    if warnings:
        err_console.print(f"\n  [dim]Total: {n_cluster} configured node(s); panel status unavailable[/dim]")
    else:
        n_panel = len(api_nodes)
        err_console.print(f"\n  [dim]Total: {n_cluster} configured node(s), {n_panel} registered in panel[/dim]")
    err_console.print()
    if warnings:
        raise typer.Exit(3)


# -- Node Remove --


def run_remove(ip_or_name: str, yes: bool = False, force: bool = False) -> None:
    """Remove a node from the fleet."""
    request = validate_command_input(NodeTargetRequest, "Invalid node remove request", ip_or_name=ip_or_name)

    cluster = load_topology_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    if not any(request.ip_or_name in {item.ip, item.name} for item in topology.nodes):
        fail(
            f"Node '{request.ip_or_name}' not found",
            hint="Check node list with: meridian node list",
            hint_type="user",
        )
    fail(
        f"Node '{request.ip_or_name}' is part of your topology",
        hint="Remove its exit or routing-gateway role in `meridian setup`, then apply the reviewed plan.",
        hint_type="user",
    )
