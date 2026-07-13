"""Node management -- add, list, check, remove proxy nodes in the fleet.

Nodes are servers running Remnawave node + Xray. All node state is
tracked in the panel database; cluster.yml stores topology for local
reference and SSH access.
"""

from __future__ import annotations

import secrets
import shlex
from typing import TYPE_CHECKING

import typer

from meridian.commands._helpers import format_traffic, load_cluster, make_panel
from meridian.commands._validation import validate_command_input
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.command_inputs import NodeAddRequest, NodeTargetRequest
from meridian.core.deploy_planning import compute_deploy_ports
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json

if TYPE_CHECKING:
    from meridian.cluster import NodeEntry
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
    from meridian.commands.resolve import ensure_server_connection, resolve_server
    from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
    from meridian.core.errors import MeridianError
    from meridian.panel_bootstrap import run_provisioner, setup_new_node
    from meridian.servers import ServerRegistry

    cluster = load_cluster()

    # Check for duplicate
    existing = cluster.find_node(request.ip)
    if existing is not None:
        fail(
            f"Node {request.ip} already exists in cluster",
            hint=f"Use: meridian deploy {request.ip} to redeploy",
            hint_type="user",
        )

    # Resolve and connect
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, explicit_ip=request.ip, user=request.user, port=request.ssh_port)
    ensure_server_connection(resolved)

    node_name = request.name or request.ip
    effective_sni = request.sni or DEFAULT_SNI

    info(f"Adding node {resolved.ip} ({node_name})...")

    if not request.yes:
        if not confirm(f"Provision and add node at {resolved.ip}?"):
            raise typer.Exit(1)

    ports = compute_deploy_ports(resolved.ip)

    # Generate paths
    xhttp_path = secrets.token_hex(8)
    ws_path = secrets.token_hex(8)

    # Run SSH provisioner pipeline (OS hardening, Docker, nginx, TLS)
    try:
        run_provisioner(
            resolved=resolved,
            cluster=cluster,
            domain=request.domain,
            sni=effective_sni,
            harden=request.harden,
            is_panel_host=False,
            secret_path=cluster.panel.secret_path,
            xhttp_port=ports.xhttp_port,
            reality_port=ports.reality_port,
            wss_port=ports.wss_port,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )

        # Configure via panel API (register node, deploy container, create hosts)
        from meridian import __version__

        setup_new_node(
            resolved=resolved,
            cluster=cluster,
            domain=request.domain,
            sni=effective_sni,
            reality_port=ports.reality_port,
            xhttp_port=ports.xhttp_port,
            wss_port=ports.wss_port,
            version=__version__,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
    except MeridianError as exc:
        fail(exc)

    # Hybrid sync — when desired_nodes is non-None, mirror this imperative
    # add into the desired list so a subsequent `meridian apply` does not
    # see the newly provisioned node as drift and propose REMOVE_NODE.
    new_node = cluster.find_node(resolved.ip)
    if new_node is not None:
        # Apply the user-requested name override (matches reconciler semantics).
        if request.name and new_node.name != request.name:
            new_node.name = request.name
            cluster.save()
        from meridian.reconciler.snapshots import hybrid_sync_desired_nodes_add

        hybrid_sync_desired_nodes_add(cluster, new_node, ssh_user=request.user, ssh_port=request.ssh_port)

    ok(f"Node {resolved.ip} provisioned and added to cluster")

    err_console.print()
    err_console.print("  [dim]List nodes:     meridian node list[/dim]")
    err_console.print("  [dim]Fleet status:   meridian fleet status[/dim]")
    err_console.print()


# -- Node Check --


def run_check(ip_or_name: str, user: str = "") -> None:
    """Check health of a node: panel status, SSH, containers, ports, TLS."""
    from meridian.diagnostics import (
        check_container_running,
        check_disk_space,
        check_port_listening,
        check_tls_certificate,
    )
    from meridian.ssh import ServerConnection, SSHError
    from meridian.ssh_ui import RichSSHUI

    request = validate_command_input(NodeTargetRequest, "Invalid node check request", ip_or_name=ip_or_name, user=user)
    cluster = load_cluster()
    node = cluster.find_node(request.ip_or_name)
    if node is None:
        fail(f"Node '{request.ip_or_name}' not found", hint="Check: meridian node list", hint_type="user")

    err_console.print()
    info(f"Checking node {node.ip} ({node.name or 'unnamed'})...")
    err_console.print()

    all_ok = True

    # 1. Panel heartbeat (command-specific — requires panel client)
    try:
        panel = make_panel(cluster)
        with panel:
            api_node = panel.get_node(node.uuid) if node.uuid else None
            if api_node and api_node.is_connected:
                ok("Panel: node connected")
            elif api_node:
                err_console.print("  [red]✗[/red] Panel: node disconnected")
                all_ok = False
            else:
                err_console.print("  [red]✗[/red] Panel: node not registered")
                all_ok = False
    except RemnawaveError:
        err_console.print("  [red]✗[/red] Panel: unreachable")
        all_ok = False

    # 2. SSH connectivity (command-specific — requires SSH auth)
    ssh_user = request.user or node.ssh_user or "root"
    try:
        conn = ServerConnection(ip=node.ip, user=ssh_user, port=node.ssh_port)
        conn.check_ssh(ui=RichSSHUI())
        ok("SSH: connected")
    except SSHError:
        err_console.print("  [red]✗[/red] SSH: cannot connect")
        warn("Cannot proceed with server-side checks")
        err_console.print()
        return

    # 3. Docker containers — via diagnostics
    container_names = ["remnawave-node"]
    if node.is_panel_host:
        container_names.extend(("remnawave", "remnawave-db", "remnawave-redis"))
    for name in container_names:
        all_ok = _render_check(check_container_running(conn, name), f"Container: {name}") and all_ok

    # 4. Port 443 — via diagnostics
    all_ok = _render_check(check_port_listening(conn, 443), "Port 443") and all_ok

    # 5. TLS cert validity — via diagnostics
    host = node.domain or node.sni or node.ip
    all_ok = _render_check(check_tls_certificate(conn, host), "TLS cert") and all_ok

    # 6. Disk space — via diagnostics
    all_ok = _render_check(check_disk_space(conn, min_free_mb=1024), "Disk") and all_ok

    err_console.print()
    if all_ok:
        ok("All checks passed")
    else:
        warn("Some checks failed — review above")
    err_console.print()


def _render_check(result: CheckResult, label: str) -> bool:
    """Render a CheckResult using console helpers. Returns True if ok."""
    if result.status == "passed":
        ok(f"{label}: {result.detail}")
        return True
    if result.status == "skipped":
        ok(f"{label}: {result.detail}")
        return True
    if result.status == "warning":
        warn(f"{label}: {result.detail}")
        if result.remediation:
            err_console.print(f"    [dim]Run: {result.remediation}[/dim]")
        return True
    # failed
    err_console.print(f"  [red]✗[/red] {label}: {result.detail}")
    if result.remediation:
        err_console.print(f"    [dim]Run: {result.remediation}[/dim]")
    return False


# -- Node Container Cleanup --


def _stop_node_containers(node: NodeEntry) -> None:
    """Best-effort SSH into node and stop containers before cluster removal."""
    from meridian.ssh import ServerConnection, SSHError
    from meridian.ssh_ui import RichSSHUI

    info(f"Stopping containers on {node.ip}...")

    try:
        conn = ServerConnection(ip=node.ip, user=node.ssh_user, port=node.ssh_port)
        conn.check_ssh(ui=RichSSHUI())
    except SSHError:
        warn(f"Could not stop containers on {node.ip} (SSH unreachable)")
        return

    node_compose = shlex.quote("/opt/remnawave-node/docker-compose.yml")
    conn.run(f"docker compose -f {node_compose} down", timeout=60)

    if node.is_panel_host:
        panel_compose = shlex.quote("/opt/remnawave/docker-compose.yml")
        conn.run(f"docker compose -f {panel_compose} down", timeout=60)

    ok("Containers stopped")


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

    cluster = load_cluster()
    panel = make_panel(cluster)

    with panel:
        try:
            api_nodes = panel.list_nodes()
        except RemnawaveError as e:
            fail(
                f"Could not query nodes: {e}",
                hint=e.hint or "Check panel connectivity",
                hint_type=e.category,
            )

    # Index API nodes by UUID for quick lookup
    api_by_uuid = {n.uuid: n for n in api_nodes}

    if is_json_mode():
        nodes_data = []
        for node in cluster.nodes:
            api_node = api_by_uuid.get(node.uuid)
            if api_node and api_node.is_connected:
                status = "connected"
            elif api_node and api_node.is_disabled:
                status = "disabled"
            else:
                status = "disconnected"
            nodes_data.append(
                {
                    "ip": node.ip,
                    "name": node.name,
                    "uuid": node.uuid,
                    "is_panel_host": node.is_panel_host,
                    "status": status,
                    "xray_version": api_node.xray_version if api_node else "",
                    "traffic_bytes": api_node.traffic_used if api_node else 0,
                }
            )
        count = len(nodes_data)
        emit_json(
            command_envelope(
                command="node.list",
                data={"nodes": nodes_data},
                summary=Summary(
                    text=f"{count} node(s) in cluster",
                    changed=False,
                    counts={"nodes": count},
                ),
                timer=operation.timer,
            )
        )
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

    for node in cluster.nodes:
        api_node = api_by_uuid.get(node.uuid)
        if api_node:
            if api_node.is_connected:
                status = "[green]connected[/green]"
            elif api_node.is_disabled:
                status = "[dim]disabled[/dim]"
            else:
                status = "[red]disconnected[/red]"
            xray = api_node.xray_version or "-"
            traffic = format_traffic(api_node.traffic_used)
        else:
            status = "[dim]unknown[/dim]"
            xray = "-"
            traffic = "-"

        label = node.name or node.ip
        if node.is_panel_host:
            label += " [dim](panel)[/dim]"

        table.add_row(node.ip, label, status, xray, traffic)

    # Show API-only nodes not in cluster config
    cluster_uuids = {n.uuid for n in cluster.nodes}
    for api_node in api_nodes:
        if api_node.uuid and api_node.uuid not in cluster_uuids:
            status = "[green]connected[/green]" if api_node.is_connected else "[red]disconnected[/red]"
            table.add_row(
                api_node.address,
                f"{api_node.name} [yellow](untracked)[/yellow]",
                status,
                api_node.xray_version or "-",
                format_traffic(api_node.traffic_used),
            )

    err_console.print()
    err_console.print(table)
    n_cluster = len(cluster.nodes)
    n_panel = len(api_nodes)
    err_console.print(f"\n  [dim]Total: {n_cluster} node(s) in cluster, {n_panel} registered in panel[/dim]")
    err_console.print()


# -- Node Remove --


def run_remove(ip_or_name: str, yes: bool = False, force: bool = False) -> None:
    """Remove a node from the fleet."""
    request = validate_command_input(NodeTargetRequest, "Invalid node remove request", ip_or_name=ip_or_name)

    cluster = load_cluster()

    node = cluster.find_node(request.ip_or_name)
    if node is None:
        fail(
            f"Node '{request.ip_or_name}' not found",
            hint="Check node list with: meridian node list",
            hint_type="user",
        )

    if node.is_panel_host:
        fail(
            "Cannot remove the panel node",
            hint="The panel runs on this node. Use 'meridian teardown' instead.",
            hint_type="user",
        )

    # Guard: check for relays that depend on this node as their exit
    dependent_relays = [r for r in cluster.relays if r.exit_node_ip == node.ip]
    if dependent_relays:
        relay_names = ", ".join(r.name or r.ip for r in dependent_relays)
        if not force:
            fail(
                f"Cannot remove node {node.ip} — {len(dependent_relays)} relay(s) depend on it: {relay_names}",
                hint="Remove relays first, or use --force to remove anyway",
                hint_type="user",
            )
        else:
            warn(f"Force-removing node with {len(dependent_relays)} dependent relay(s): {relay_names}")

    if not yes:
        if not confirm(f"Remove node {node.ip} ({node.name or 'unnamed'})?"):
            raise typer.Exit(1)

    panel = make_panel(cluster)
    with panel:
        if node.uuid:
            try:
                panel.disable_node(node.uuid)
                info("Node disabled in panel")
            except RemnawaveError:
                warn("Could not disable node in panel (may already be disabled)")

            try:
                panel.delete_node(node.uuid)
                ok("Node removed from panel")
            except RemnawaveError as e:
                warn(f"Could not delete node from panel: {e}")

    # Best-effort SSH container cleanup before removing from cluster
    _stop_node_containers(node)

    cluster.nodes = [n for n in cluster.nodes if n.ip != node.ip]
    cluster.save()

    # Hybrid sync — drop from desired_nodes (only if managed declaratively).
    from meridian.reconciler.snapshots import hybrid_sync_desired_nodes_remove

    hybrid_sync_desired_nodes_remove(cluster, node.ip)

    ok(f"Node {node.ip} removed from cluster")

    err_console.print()
    err_console.print("  [dim]List nodes:   meridian node list[/dim]")
    err_console.print()
