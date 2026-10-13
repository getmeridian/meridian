"""Relay management -- deploy, list, remove, check relay nodes.

Relays are lightweight Realm TCP forwarders that provide domestic entry
points. All relay-to-panel mapping uses Remnawave Host entries; topology
is persisted in cluster.yml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.commands._helpers import (
    load_topology_cluster,
    make_panel,
    persist_reviewed_apply,
    reviewed_apply_persistence,
)
from meridian.commands._validation import validate_command_input
from meridian.commands.apply_progress import apply_progress
from meridian.config import HOME_DISPLAY
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.command_inputs import RelayDeployRequest, RelayTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.errors import MeridianError as MeridianException
from meridian.core.fleet import build_relay_list_result
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.remnawave import RemnawaveError
from meridian.ssh import SSHError
from meridian.ssh_ui import RichSSHUI

if TYPE_CHECKING:
    from meridian.servers import ServerRegistry


def run_deploy(
    relay_ip: str,
    exit_arg: str,
    user: str = "",
    relay_name: str = "",
    listen_port: int = 443,
    yes: bool = False,
    sni: str = "",
    ssh_port: int = 0,
) -> None:
    """Deploy a relay node that forwards traffic to an exit server."""
    from meridian.commands.resolve import resolve_server
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.editor import add_relay_to_intent
    from meridian.setup.runtime import SetupRuntime

    target = resolve_server(ServerRegistry(SERVER_PROFILES_FILE), explicit_ip=relay_ip, user=user, port=ssh_port)
    request = validate_command_input(
        RelayDeployRequest,
        "Invalid relay add request",
        relay_ip=target.ip,
        exit_arg=exit_arg,
        user=target.user,
        relay_name=relay_name,
        listen_port=listen_port,
        yes=yes,
        sni=sni,
        ssh_port=target.port,
    )

    cluster = load_topology_cluster()
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    exit_ref = ""
    try:
        for exit_ in intent.exits:
            entry = registry.find(exit_.server_ref)
            selectors = {
                exit_.id,
                exit_.server_ref,
                *({entry.host, entry.name} if entry is not None else set()),
            }
            if request.exit_arg in selectors:
                exit_ref = exit_.id
                break
    except LocalStateError as exc:
        fail(exc)
    if not exit_ref:
        fail(
            f"Exit {request.exit_arg!r} was not found in your topology.",
            hint="Run `meridian setup` to review exit roles.",
            hint_type="user",
        )
    if not request.yes and not confirm(f"Add {request.relay_name or request.relay_ip} as a relay to {exit_ref}?"):
        raise typer.Exit(1)

    connection = target.conn
    try:
        connection.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    try:
        registry.add(
            ServerEntry(
                host=request.relay_ip,
                user=request.user,
                name=request.relay_name or request.relay_ip,
                port=request.ssh_port,
                auth_state="validated",
                machine_id=target.machine_id,
            )
        )
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save the V4 relay server profile: {exc}",
            hint=f"Check {HOME_DISPLAY} permissions and disk space, then retry.",
            hint_type="system",
        )
    try:
        entry = registry.find(request.relay_ip)
    except LocalStateError as exc:
        fail(exc)
    if entry is None:
        raise RuntimeError("Validated relay server was not saved")
    existing = next((relay for relay in intent.transparent_relays if relay.hop_server_refs[0] == entry.id), None)
    if existing is None:
        updated = add_relay_to_intent(
            intent,
            server_ref=entry.id,
            title=(request.relay_name or f"relay-{len(intent.transparent_relays) + 1}"),
            exit_ref=exit_ref,
            listen_port=request.listen_port,
            reality_sni=request.sni,
        )
    elif (existing.exit_ref, existing.listen_port) != (exit_ref, request.listen_port) or (
        request.sni and request.sni != existing.reality_sni
    ):
        fail(
            f"{request.relay_name or request.relay_ip} already relays to {existing.exit_ref} "
            f"on port {existing.listen_port}.",
            hint=f"Remove it first with `meridian relay remove {existing.id}`, then add it again.",
            hint_type="user",
        )
    else:
        # A re-run after a failed or interrupted add resumes the declared relay.
        updated = intent
    try:
        with reviewed_apply_persistence("V4 relay apply"), apply_progress(registry=registry) as reporter:
            result = SetupRuntime(
                registry,
                cluster_loader=lambda: cluster,
                persist=persist_reviewed_apply,
                reporter=reporter,
            ).apply_intent(updated)
    except MeridianException as exc:
        fail(exc)
    if not result.all_succeeded:
        failures = result.failure_summary
        fail(
            "V4 relay did not converge.",
            hint=failures,
            hint_type="system",
        )
    ok(f"Relay {request.relay_name or request.relay_ip} {'added' if result.changed else 'already converged'}")


def run_list(
    exit_arg: str = "",
) -> None:
    """List relay nodes from cluster configuration."""
    from meridian.console import error_context

    operation = OperationContext()
    with error_context("relay.list", timer=operation.timer):
        _run_list(exit_arg=exit_arg, operation=operation)


def _run_list(
    *,
    exit_arg: str = "",
    operation: OperationContext,
) -> None:
    """Implementation for relay list with command metadata attached."""
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_topology_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)

    relays = topology.relays
    if exit_arg:
        exit_node = next(
            (node for node in topology.nodes if node.ip == exit_arg or node.name == exit_arg),
            None,
        )
        if exit_node is None:
            fail(
                f"Exit node '{exit_arg}' not found in cluster",
                hint="List nodes: meridian node list",
                hint_type="user",
            )
        exit_ip = exit_node.ip
        relays = [r for r in relays if r.exit_node_ip == exit_ip]

    from meridian.console import is_json_mode

    if not relays and not is_json_mode():
        info(f"No relays {'attached to exit ' + exit_arg if exit_arg else 'configured'}")
        err_console.print("\n  [dim]Deploy one: meridian relay add RELAY_IP --exit EXIT_IP[/dim]\n")
        return

    # Optionally check host status from panel
    host_status: dict[tuple[str, int], bool | None] = {}
    warnings: list[MeridianError] = []
    if relays:
        try:
            with make_panel(cluster) as panel:
                host_map = {h.uuid: h for h in panel.list_hosts()}
                for relay in relays:
                    endpoint = (relay.ip, relay.port)
                    for host_ref in relay.host_refs:
                        h = host_map.get(host_ref.uuid)
                        if h and (endpoint not in host_status or not host_status[endpoint]):
                            host_status[endpoint] = not h.is_disabled
        except RemnawaveError as exc:
            warnings.append(
                MeridianError(
                    code="MERIDIAN_RELAY_HOST_STATUS_UNAVAILABLE",
                    category="system",
                    message="Relay topology is available, but panel host status could not be collected.",
                    hint=exc.hint or "Check panel connectivity and retry.",
                    retryable=True,
                    exit_code=3,
                )
            )
            if not is_json_mode():
                warn("Panel host status is unavailable; relay rows are shown as unknown")

    # JSON output
    if is_json_mode():
        from meridian.renderers import emit_json

        result = build_relay_list_result(relays, host_status)
        count = len(result.relays)
        exit_code = 3 if warnings else 0
        emit_json(
            command_envelope(
                command="relay.list",
                data=result.to_data(),
                summary=Summary(
                    text=f"{count} relay(s) configured",
                    changed=False,
                    counts={"relays": count},
                ),
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            )
        )
        if exit_code:
            raise typer.Exit(exit_code)
        return

    title = f"Relays for {exit_arg}" if exit_arg else "All Relay Nodes"
    table = Table(title=title, show_lines=False, pad_edge=False, box=ROUNDED, padding=(0, 2))
    for col, kw in [
        ("Relay IP", {"style": "bold cyan"}),
        ("Name", {"style": "dim"}),
        ("Exit", {"style": "bold"}),
        ("Port", {"justify": "right"}),
        ("SNI", {"style": "dim"}),
        ("Status", {"justify": "center"}),
    ]:
        table.add_column(col, **kw)  # type: ignore[arg-type]

    for relay in relays:
        enabled = host_status.get((relay.ip, relay.port))
        if enabled:
            status_str = "[green]enabled[/green]"
        elif enabled is False:
            status_str = "[dim]disabled[/dim]"
        else:
            status_str = "[dim]-[/dim]"
        table.add_row(
            escape(relay.ip),
            escape(relay.name) if relay.name else "-",
            escape(relay.exit_node_ip),
            str(relay.port),
            escape(relay.sni) if relay.sni else "-",
            status_str,
        )

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {len(relays)} relay(s)[/dim]\n")
    if warnings:
        raise typer.Exit(3)


def select_relay_chains(
    intent: SetupIntent,
    registry: ServerRegistry,
    query: str,
    exit_arg: str = "",
) -> tuple[list[TransparentRelayIntent], str, str]:
    """Resolve a relay server (IP or saved name) or chain ID to its chains, server ref, and label."""
    try:
        entry = registry.find(query)
        exit_entries = {exit_.id: registry.find(exit_.server_ref) for exit_ in intent.exits} if exit_arg else {}
    except LocalStateError as exc:
        fail(exc)
    server_ref = entry.id if entry is not None else ""
    chains = [
        relay
        for relay in intent.transparent_relays
        if relay.id == query or (server_ref and server_ref in relay.hop_server_refs)
    ]
    if exit_arg:
        exits = {
            exit_.id
            for exit_ in intent.exits
            if exit_arg
            in {
                exit_.id,
                exit_.server_ref,
                *({found.host, found.name} if (found := exit_entries.get(exit_.id)) is not None else set()),
            }
        }
        chains = [relay for relay in chains if relay.exit_ref in exits]
    if not chains:
        scope = f" toward exit {exit_arg}" if exit_arg else ""
        fail(f"Relay {query} not found in the topology{scope}", hint="Check: meridian relay list", hint_type="user")
    label = (entry.name or entry.host) if entry is not None else query
    return chains, server_ref, label


def run_remove(
    relay_ip: str,
    exit_arg: str = "",
    yes: bool = False,
) -> None:
    """Drop every relay chain through a relay server (or one chain by ID), then apply the reviewed removal."""
    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay remove request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        yes=yes,
    )
    from meridian.commands.topology_removal import apply_topology_removal
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import remove_relays_from_intent

    cluster = load_topology_cluster()
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    chains, _server_ref, label = select_relay_chains(
        intent,
        ServerRegistry(SERVER_PROFILES_FILE),
        request.relay_ip,
        request.exit_arg,
    )
    chain_ids = sorted(relay.id for relay in chains)
    try:
        updated = remove_relays_from_intent(intent, set(chain_ids))
    except ValueError as exc:
        fail(str(exc), hint="Check: meridian relay list", hint_type="user")
    noun = "relay chain" if len(chain_ids) == 1 else "relay chains"
    subject = f"{noun} {', '.join(chain_ids)}" + (f" through {label}" if label not in chain_ids else "")
    hop_counts = [len(relay.hop_server_refs) for relay in chains]
    extra = (f"Every hop of the chain stops forwarding ({sum(hop_counts)} hops in total).",)
    apply_topology_removal(cluster, updated, subject=subject, yes=request.yes, extra_lines=extra)
