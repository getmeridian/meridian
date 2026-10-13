"""`meridian node replace` -- move one exit to another server as a reviewed intent edit."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.commands._validation import validate_command_input
from meridian.console import confirm, err_console, fail, ok
from meridian.core.command_inputs import NodeTargetRequest
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.topology import ExitIntent, SetupIntent

if TYPE_CHECKING:
    from meridian.servers import ServerRegistry


def run_replace(old: str, new: str, *, name: str = "", user: str = "", ssh_port: int = 0, yes: bool = False) -> None:
    """Move exit OLD onto server NEW, keeping its ID, keys, paths, settings, and relays."""
    request = validate_command_input(NodeTargetRequest, "Invalid node replace request", ip_or_name=old)
    from meridian.commands.resolve import resolve_server
    from meridian.commands.server import register_server
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.core.setup import SetupDraft
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import move_exit_in_intent

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    exit_ = _exit_to_move(intent, registry, request.ip_or_name)
    old_label = _label(registry, exit_.server_ref)

    target = resolve_server(registry, explicit_ip=new, user=user, port=ssh_port)
    try:
        roles = registry.roles(target.ip, cluster)
    except LocalStateError as exc:
        fail(exc)
    if roles:
        fail(
            f"{target.ip} already serves {', '.join(roles)}",
            hint="Replace an exit with a server that has no role yet; free this one with `meridian node remove` first.",
            hint_type="user",
        )
    entry = register_server(registry, target, name=name)
    if entry.id == exit_.server_ref:
        fail(f"Exit {exit_.id} already runs on {old_label}", hint_type="user")
    new_label = entry.name or entry.host
    try:
        updated = move_exit_in_intent(intent, exit_.id, entry.id)
    except ValueError as exc:
        fail(f"Cannot move exit {exit_.id}: {exc}", hint="Adjust the topology in `meridian setup`.", hint_type="user")

    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
    except MeridianError as exc:
        fail(exc)

    subject = f"exit {exit_.id} from {old_label} to {new_label}"
    relays = sorted(relay.id for relay in intent.transparent_relays if relay.exit_ref == exit_.id)
    err_console.print()
    err_console.print(f"  [bold]Move {escape(subject)}[/bold]")
    err_console.print("  Keeps the exit's ID, Reality keys, protocol paths, SNI, WARP, and geo settings.")
    if relays:
        err_console.print(f"  Relays re-pointed to the new server: {escape(', '.join(relays))}")
    err_console.print(f"  Deploys it on {escape(new_label)}; clients pick up the new address on their next refresh.")
    if review.retiring:
        err_console.print(f"  Then cleans up {len(review.retiring)} resources on the old server:")
        for binding in review.retiring:
            where = _label(registry, binding.server_ref) if binding.server_ref else "panel"
            err_console.print(f"    [red]remove[/red] {escape(binding.logical_id)} [dim]({escape(where)})[/dim]")
    err_console.print(f"  [dim]Review {review.review_hash}[/dim]")
    err_console.print()
    if not yes and not confirm(f"Move {subject}?"):
        raise typer.Exit(1)

    result = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=f"Exit {exit_.id} was not fully moved",
        hint=f"The topology already places it on {new_label}; rerun `meridian apply` to finish the move.",
        expected_plan_hash=review.review_hash,
    )
    ok(f"Moved {subject} ({len(result.retired)} old resources cleaned up). {old_label} stays in your saved servers.")


def _exit_to_move(intent: SetupIntent, registry: ServerRegistry, query: str) -> ExitIntent:
    """Find the exit by ID or by its saved server, refusing the control server and non-exits."""
    try:
        entry = registry.find(query)
    except LocalStateError as exc:
        fail(exc)
    server_ref = entry.id if entry is not None else ""
    exit_ = next((item for item in intent.exits if query == item.id or item.server_ref == server_ref), None)
    if (exit_.server_ref if exit_ is not None else server_ref) == intent.control.server_ref:
        fail(
            f"{query} runs the control plane, which `node replace` cannot move yet",
            hint=(
                "Moving the panel changes every subscription URL. Add a new exit with `meridian node add` "
                "and point clients at it instead."
            ),
            hint_type="user",
        )
    if exit_ is None:
        fail(f"Exit '{query}' not found", hint="Check node list with: meridian node list", hint_type="user")
    return exit_


def _label(registry: ServerRegistry, server_ref: str) -> str:
    try:
        entry = registry.find(server_ref)
    except LocalStateError:
        entry = None
    return (entry.name or entry.host) if entry is not None else server_ref
