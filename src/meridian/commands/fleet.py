"""Fleet status -- overview of all nodes, relays, and users.

Single command that shows the health of the entire Meridian cluster
at a glance: panel connectivity, node status, relay reachability, user count.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

import typer
from rich.markup import escape

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.commands._helpers import format_traffic, load_cluster, make_panel
from meridian.console import err_console, error_context, fail, is_json_mode, warn
from meridian.core.errors import LocalStateError
from meridian.core.fleet import FleetStatus, TopologyRelay
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.services.fleet import collect_fleet_inventory, collect_fleet_status
from meridian.remnawave import RemnawaveAuthError, RemnawaveError
from meridian.renderers import emit_json


def _check_relay_health(ip: str, port: int, timeout: float = 3.0) -> bool:
    """TCP connect check to verify relay is reachable."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _check_relays_health(
    relays: list[TopologyRelay], *, timeout: float = 3.0, max_workers: int = 8
) -> dict[tuple[str, int], bool]:
    """Check relay health concurrently with a bounded worker pool."""
    if not relays:
        return {}
    results: dict[tuple[str, int], bool] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(relays))) as executor:
        futures = {
            executor.submit(_check_relay_health, relay.ip, relay.port, timeout): (relay.ip, relay.port)
            for relay in relays
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _classify_panel_error(exc: Exception) -> Literal["auth", "system", "bug"]:
    """Tell core services which panel errors must abort instead of warn."""
    if isinstance(exc, RemnawaveAuthError):
        return "auth"
    if isinstance(exc, RemnawaveError):
        return "system"
    return "bug"


def _render_warnings(warnings: list[MeridianError]) -> None:
    """Render non-fatal service warnings in human mode."""
    if is_json_mode():
        return
    for warning in warnings:
        warn(warning.message)


# -- Fleet Inventory --


def run_inventory() -> None:
    """Show the configured fleet inventory without exposing secrets."""
    operation = OperationContext()
    with error_context("fleet.inventory", timer=operation.timer):
        _run_inventory(operation=operation)


def _run_inventory(*, operation: OperationContext) -> None:
    """Implementation for inventory with command metadata already attached."""
    cluster = load_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    try:
        result = collect_fleet_inventory(
            topology,
            make_panel(cluster),
            classify_error=_classify_panel_error,
        )
    except RemnawaveAuthError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    except Exception as exc:  # Top-level command boundary — unexpected errors are bugs
        fail(f"Could not collect fleet inventory: {exc}", hint_type="bug")

    inventory = result.inventory
    warnings = result.warnings
    _render_warnings(warnings)
    data = inventory.to_data()
    exit_code = 3 if warnings else 0

    if is_json_mode():
        emit_json(
            command_envelope(
                command="fleet.inventory",
                data=data,
                summary=Summary(
                    text=inventory.summary.text,
                    changed=False,
                    counts={
                        "nodes": inventory.summary.nodes,
                        "relays": inventory.summary.relays,
                        "pending_desired_resources": inventory.summary.pending,
                    },
                ),
                status="ok",
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            )
        )
        if exit_code:
            raise typer.Exit(exit_code)
        return

    err_console.print()
    status = "[green]healthy[/green]" if inventory.panel.healthy else "[red]UNREACHABLE[/red]"
    err_console.print(f"  [bold]Panel[/bold]   {escape(inventory.panel.url)}  {status}")
    if cluster.panel.server_ip:
        err_console.print(
            f"            SSH {cluster.panel.ssh_user}@{cluster.panel.server_ip}:{cluster.panel.ssh_port}"
        )

    err_console.print()
    err_console.print("  [bold]Servers[/bold]")
    for server in inventory.servers:
        roles = ",".join(server.roles)
        hops = ", ".join(
            f"{escape(hop.chain)} hop {hop.position}{' (entry)' if hop.advertised else ''}: {hop.health}"
            for hop in server.relay_hops
        )
        suffix = f"  {hops}" if hops else ""
        err_console.print(f"    {escape(server.ip)}  {escape(server.name) if server.name else '-'}  {roles}{suffix}")

    err_console.print()
    err_console.print("  [bold]Nodes[/bold]")
    if not inventory.nodes:
        err_console.print("    [dim]none[/dim]")
    for display_node in inventory.nodes:
        desired = (
            "" if display_node.desired is None else "  desired" if display_node.desired else "  [yellow]extra[/yellow]"
        )
        domain = f"  domain={escape(display_node.domain)}" if display_node.domain else ""
        err_console.print(
            f"    {escape(display_node.ip)}  {escape(display_node.name) if display_node.name else '-'}  "
            f"{display_node.role}  "
            f"{display_node.panel_status}{domain}{desired}"
        )

    err_console.print()
    err_console.print("  [bold]Relays[/bold]")
    if not inventory.relays:
        err_console.print("    [dim]none[/dim]")
    for display_relay in inventory.relays:
        target = display_relay.exit_node_name or display_relay.exit_node_ip
        desired = (
            ""
            if display_relay.desired is None
            else "  desired"
            if display_relay.desired
            else "  [yellow]extra[/yellow]"
        )
        err_console.print(
            f"    {escape(display_relay.ip)}  {escape(display_relay.name) if display_relay.name else '-'}  "
            f":{display_relay.port} -> {escape(target)}{desired}"
        )

    if inventory.summary.desired_nodes or inventory.summary.desired_relays:
        err_console.print()
        err_console.print(
            "  [bold]Desired[/bold] "
            f"{inventory.summary.desired_nodes} node(s), {inventory.summary.desired_relays} relay(s); "
            f"{inventory.summary.pending} pending"
        )
    err_console.print()
    if exit_code:
        raise typer.Exit(exit_code)


# -- Fleet Status --


def _render_status(status: FleetStatus) -> None:
    """Render fleet status for humans from the typed core result."""
    err_console.print()
    panel_state = "[green]healthy[/green]" if status.panel.healthy else "[red]UNREACHABLE[/red]"
    err_console.print(f"  [bold]Panel[/bold]   {escape(status.panel.url)} {panel_state}")

    if status.nodes:
        err_console.print()
        err_console.print("  [bold]Nodes[/bold]")

        for node in status.nodes:
            label = escape(node.name or node.ip)
            role = "  [dim](panel)[/dim]" if node.is_panel_host else ""
            if node.role == "routing_gateway":
                role += "  [dim](routing gateway)[/dim]"

            if node.status == "connected":
                xray = f"  Xray {escape(node.xray_version)}" if node.xray_version else ""
                traffic = f"  {format_traffic(node.traffic_bytes)}" if node.traffic_bytes else ""
                err_console.print(f"    {escape(node.ip)}  {label}{role}  [green]connected[/green]{xray}{traffic}")
            elif node.status == "disabled":
                err_console.print(f"    {escape(node.ip)}  {label}{role}  [dim]disabled[/dim]")
            elif node.status == "disconnected":
                err_console.print(f"    {escape(node.ip)}  {label}{role}  [red]DISCONNECTED[/red]")
            else:
                err_console.print(f"    {escape(node.ip)}  {label}{role}  [dim]unknown[/dim]")

    if status.relays:
        err_console.print()
        err_console.print("  [bold]Relays[/bold]")

        for relay in status.relays:
            label = escape(relay.name or relay.ip)
            target_label = escape(relay.exit_node_name or relay.exit_node_ip)
            if relay.healthy is True:
                relay_state = "[green]reachable[/green] [dim](route unverified)[/dim]"
            elif relay.healthy is False:
                relay_state = "[red]UNREACHABLE[/red]"
            else:
                relay_state = "[dim]unknown[/dim]"

            err_console.print(f"    {escape(relay.ip)}  {label} -> {target_label}  listener: {relay_state}")

    internal_hops = [(server, hop) for server in status.servers for hop in server.relay_hops if not hop.advertised]
    if internal_hops:
        err_console.print()
        err_console.print("  [bold]Internal relay hops[/bold]")
        for server, hop in internal_hops:
            err_console.print(
                f"    {escape(server.ip)}  {escape(server.name) if server.name else '-'}  "
                f"{escape(hop.chain)} hop {hop.position}  "
                "[dim]not externally probed[/dim]"
            )

    if status.summary.users or status.summary.missing_access_users or status.summary.nonactive_access_users:
        err_console.print()
        parts = [f"{status.summary.active_users} active"]
        if status.summary.disabled_users:
            parts.append(f"{status.summary.disabled_users} disabled")
        if status.summary.other_users:
            parts.append(f"{status.summary.other_users} other")
        if status.summary.missing_access_users:
            parts.append(f"{status.summary.missing_access_users} missing")
        if status.summary.nonactive_access_users:
            parts.append(f"{status.summary.nonactive_access_users} declared non-active")
        err_console.print(f"  [bold]Users[/bold]   {', '.join(parts)}")

    err_console.print()


def run_status() -> int:
    """Show fleet health overview: panel, nodes, relays, users."""
    operation = OperationContext()
    with error_context("fleet.status", timer=operation.timer):
        return _run_status(operation=operation)


def _run_status(*, operation: OperationContext) -> int:
    """Implementation for status with command metadata already attached."""
    cluster = load_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    try:
        result = collect_fleet_status(
            topology,
            make_panel(cluster),
            check_relays=_check_relays_health,
            classify_error=_classify_panel_error,
        )
    except RemnawaveAuthError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    except Exception as exc:  # Top-level command boundary — unexpected errors are bugs
        fail(f"Could not collect fleet status: {exc}", hint_type="bug")

    status = result.status
    warnings = result.warnings
    _render_warnings(warnings)
    exit_code = {"healthy": 0, "degraded": 4, "unknown": 3}[status.summary.health]

    if is_json_mode():
        emit_json(
            command_envelope(
                command="fleet.status",
                data=status.to_data(),
                summary=Summary(
                    text=status.summary.text,
                    changed=False,
                    counts={
                        "nodes": status.summary.nodes,
                        "relays": status.summary.relays,
                        "users": status.summary.users,
                        "missing_access_users": status.summary.missing_access_users,
                        "nonactive_access_users": status.summary.nonactive_access_users,
                        "unhealthy_relays": status.summary.unhealthy_relays,
                        "unknown_relays": status.summary.unknown_relays,
                        "unknown_relay_hops": status.summary.unknown_relay_hops,
                        "disconnected_nodes": status.summary.disconnected_nodes,
                        "disabled_nodes": status.summary.disabled_nodes,
                        "unknown_nodes": status.summary.unknown_nodes,
                    },
                ),
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            )
        )
        return exit_code

    _render_status(status)
    return exit_code
