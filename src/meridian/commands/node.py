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
    apply_reviewed_intent,
    format_traffic,
    load_topology_cluster,
    require_intent,
)
from meridian.commands._validation import validate_command_input
from meridian.config import HOME_DISPLAY
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.control_access import fleet_panel
from meridian.core.command_inputs import NodeAddRequest, NodeTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.fleet import build_node_list_result
from meridian.core.models import ErrorModel, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.topology import ExitIntent, RoutingGatewayIntent, SetupIntent
from meridian.registry_country import registry_country
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json

if TYPE_CHECKING:
    from meridian.resolve import ResolvedServer


def run_add(
    ip: str,
    name: str = "",
    user: str = "",
    ssh_port: int = 0,
    sni: str = "",
    domain: str = "",
    country: str = "",
    harden: bool = True,
    yes: bool = False,
    self_steal: bool = False,
) -> None:
    """Provision and add a new node to the fleet."""
    from meridian.commands.resolve import (
        ensure_server_connection,
        resolve_server,
    )
    from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.editor import add_exit_to_intent

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    target = resolve_server(registry, explicit_ip=ip, user=user, port=ssh_port)
    try:
        saved = registry.find(target.ip)
    except LocalStateError as exc:
        fail(exc)
    request = validate_command_input(
        NodeAddRequest,
        "Invalid node add request",
        ip=target.ip,
        # A saved server keeps its title (saving it as its IP renamed it) and names the exit.
        name=name or (saved.name if saved is not None else ""),
        user=target.user,
        ssh_port=target.port,
        sni=sni,
        domain=domain,
        country=country,
        harden=harden,
        yes=yes,
        self_steal=self_steal,
    )
    if request.sni and not request.self_steal:
        from meridian.commands.sni import refuse_unusable_camouflage

        refuse_unusable_camouflage(request.sni)

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    if not request.harden:
        fail(
            "Fleet servers always keep OS hardening enabled.",
            hint=("Use `meridian setup` to review topology-managed server choices."),
            hint_type="user",
        )
    if not request.yes and not confirm(f"Add {request.name or request.ip} as an exit?"):
        raise typer.Exit(1)

    resolved = ensure_server_connection(target)
    try:
        registry.add(
            ServerEntry(
                host=resolved.ip,
                user=resolved.user,
                name=request.name or resolved.ip,
                port=request.ssh_port,
                auth_state="validated",
                machine_id=resolved.machine_id,
            )
        )
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save the server profile: {exc}",
            hint=f"Check {HOME_DISPLAY} permissions and disk space, then retry.",
            hint_type="system",
        )
    try:
        entry = registry.find(resolved.ip)
    except LocalStateError as exc:
        fail(exc)
    if entry is None:
        raise RuntimeError("Validated server was not saved")
    existing = next((exit_ for exit_ in intent.exits if exit_.server_ref == entry.id), None)
    if request.self_steal:
        from meridian.commands.sni import check_own_hostname

        check_own_hostname(resolved.conn, request.reality_sni, server_ip=resolved.ip, label=request.name or resolved.ip)
    if existing is None:
        updated = add_exit_to_intent(
            intent,
            server_ref=entry.id,
            title=request.name or f"exit-{len(intent.exits) + 1}",
            reality_sni=request.reality_sni or _neighbor_sni(resolved) or DEFAULT_SNI,
            tls_hostname=request.domain,
            address=resolved.ip,
            region=request.country or registry_country(resolved.ip),
            self_steal=request.self_steal,
        )
    else:
        # A failed or interrupted add leaves its exit in the intent; re-running resumes it.
        saved_snis = {path.reality_sni for path in existing.paths if path.reality_sni}
        saved_domains = {path.tls_sni for path in existing.paths if path.tls_sni}
        saved_self_steal = any(path.self_steal for path in existing.paths)
        if (
            (request.reality_sni and request.reality_sni not in saved_snis)
            or (request.domain and request.domain not in saved_domains)
            or (request.self_steal and not saved_self_steal)
        ):
            fail(
                f"{resolved.ip} is already exit {existing.id} with different settings",
                hint="Change an existing exit's SNI with `meridian node sni`, or its domain with `meridian setup`.",
                hint_type="user",
            )
        updated = intent
        if request.country and request.country != existing.region:
            # Correcting the country of an existing exit is a naming edit, not a conflict.
            updated = SetupIntent.model_validate(
                {
                    **intent.model_dump(mode="json"),
                    "exits": [
                        {**exit_.model_dump(mode="json"), "region": request.country}
                        if exit_.id == existing.id
                        else exit_.model_dump(mode="json")
                        for exit_ in intent.exits
                    ],
                }
            )
    result = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=f"Exit {request.name or resolved.ip} did not converge",
        hint="The exit stays in the topology; fix the cause and rerun this command or `meridian apply`.",
    )
    ok(f"Exit {request.name or resolved.ip} {'added' if result.changed else 'already converged'}")


def run_list() -> None:
    """List all nodes with health status from the panel."""
    from meridian.console import error_context

    operation = OperationContext()
    with error_context("node.list", timer=operation.timer):
        _run_list(operation=operation)


def _run_list(*, operation: OperationContext) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    from meridian.console import is_json_mode

    cluster = load_topology_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    api_nodes = []
    warnings: list[ErrorModel] = []
    try:
        with fleet_panel(cluster, topology) as panel:
            api_nodes = panel.list_nodes()
    except RemnawaveError as exc:
        warnings.append(
            ErrorModel(
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


def run_remove(ip_or_name: str, yes: bool = False, force: bool = False, forget_unreachable: bool = False) -> None:
    """Remove a server's exit or routing-gateway role, then apply the reviewed removal."""
    request = validate_command_input(NodeTargetRequest, "Invalid node remove request", ip_or_name=ip_or_name)
    from meridian.commands.topology_removal import apply_topology_removal
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    try:
        entry = ServerRegistry(SERVER_PROFILES_FILE).find(request.ip_or_name)
    except LocalStateError as exc:
        fail(exc)
    server_ref = entry.id if entry is not None else ""
    label = (entry.name or entry.host) if entry is not None else request.ip_or_name
    exit_ = next(
        (item for item in intent.exits if request.ip_or_name == item.id or item.server_ref == server_ref),
        None,
    )
    gateway = next(
        (item for item in intent.routing_gateways if request.ip_or_name == item.id or item.server_ref == server_ref),
        None,
    )
    role = exit_ or gateway
    server_ref = server_ref or (role.server_ref if role is not None else "")

    if server_ref and server_ref == intent.control.server_ref:
        fail(
            f"{label} runs the control plane, so it cannot leave the fleet on its own",
            hint="Remove the other exits, relays, and gateways first; then `meridian teardown` removes the fleet.",
            hint_type="user",
        )
    if exit_ is not None:
        updated, subject, extra_lines = _exit_removal(intent, exit_, label, force=force)
    elif gateway is not None:
        updated, subject, extra_lines = _gateway_removal(intent, gateway, label)
    elif server_ref and any(server_ref in relay.hop_server_refs for relay in intent.transparent_relays):
        fail(
            f"{label} is a relay hop, not a node",
            hint=f"Remove its relay chain with: meridian relay remove {request.ip_or_name}",
            hint_type="user",
        )
    else:
        fail(
            f"Node '{request.ip_or_name}' not found", hint="Check node list with: meridian node list", hint_type="user"
        )
    apply_topology_removal(
        cluster, updated, subject=subject, yes=yes, extra_lines=extra_lines, forget_unreachable=forget_unreachable
    )


def _exit_removal(
    intent: SetupIntent,
    exit_: ExitIntent,
    label: str,
    *,
    force: bool,
) -> tuple[SetupIntent, str, tuple[str, ...]]:
    """Drop one exit (and, with --force, the relay chains into it) or explain what still depends on it."""
    from meridian.setup.editor import remove_exit_from_intent, remove_relays_from_intent

    relays = sorted(relay.id for relay in intent.transparent_relays if relay.exit_ref == exit_.id)
    if relays and not force:
        fail(
            f"Relays still route to exit {exit_.id}: {', '.join(relays)}",
            hint="Remove them first with `meridian relay remove RELAY`, or rerun with --force to remove them too.",
            hint_type="user",
        )
    path_ids = {path.id for path in exit_.paths}
    bridged = sorted(gateway.id for gateway in intent.routing_gateways if gateway.bridge_path_ref in path_ids)
    if bridged:
        fail(
            f"Routing gateway {', '.join(bridged)} enters the fleet through exit {exit_.id}",
            hint="Remove the gateway first with `meridian node remove GATEWAY`, or move its bridge path in setup.",
            hint_type="user",
        )
    extra_lines: tuple[str, ...] = ()
    try:
        updated = intent
        if relays:
            updated = remove_relays_from_intent(updated, set(relays))
            extra_lines = (f"Also removes relay chains: {', '.join(relays)}",)
        updated = remove_exit_from_intent(updated, exit_.id)
    except ValueError as exc:
        fail(
            f"Cannot remove exit {exit_.id}: {exc}",
            hint="Adjust egress pools, routing rules, or the default egress in `meridian setup`, then retry.",
            hint_type="user",
        )
    return updated, f"exit {exit_.id} on {label}", extra_lines


def _gateway_removal(
    intent: SetupIntent,
    gateway: RoutingGatewayIntent,
    label: str,
) -> tuple[SetupIntent, str, tuple[str, ...]]:
    """Drop one routing gateway and the routing rules scoped to it."""
    from meridian.setup.editor import remove_routing_gateway_from_intent

    scoped = sorted(route.id for route in intent.routes if route.source_gateway_ref == gateway.id)
    try:
        updated = remove_routing_gateway_from_intent(intent, gateway.id)
    except ValueError as exc:
        fail(
            f"Cannot remove routing gateway {gateway.id}: {exc}",
            hint="Remove or rescope the routing rules in `meridian setup`, then retry.",
            hint_type="user",
        )
    extra_lines = (f"Also drops routing rules scoped to it: {', '.join(scoped)}",) if scoped else ()
    return updated, f"routing gateway {gateway.id} on {label}", extra_lines


def _neighbor_sni(resolved: ResolvedServer) -> str:
    """A camouflage site in the new server's own subnet, or "" to use the default target."""
    from meridian.diagnostics.camouflage import neighbor_camouflage_targets

    info("Looking for a camouflage site in the server's own network (about a minute)...")
    found = neighbor_camouflage_targets(resolved.conn, resolved.ip, limit=1)
    if found:
        ok(f"Camouflage site next to the server: {found[0]}")
    return found[0] if found else ""
