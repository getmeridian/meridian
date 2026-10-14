"""`meridian route` -- per-destination egress overrides on exits (WARP, direct, block)."""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent
from meridian.console import confirm, err_console, error_context, fail, is_json_mode, ok
from meridian.core.errors import LocalStateError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.routes import RouteMutationResult, build_route_list_result
from meridian.core.topology import EgressOverrideAction, EgressOverrideIntent, ExitIntent, SetupIntent
from meridian.renderers import emit_json

_VIA: dict[str, EgressOverrideAction] = {"warp": "warp", "direct": "direct", "block": "block"}


def run_add(exit_arg: str, values: list[str], via: str, name: str = "") -> None:
    """Add one override to an exit and apply the reviewed topology."""
    from meridian.setup.editor import add_egress_override_to_intent

    operation = OperationContext()
    with error_context("route.add", timer=operation.timer):
        action = _VIA.get(via.strip().lower())
        if action is None:
            fail(f"Unknown --via {via!r}", hint="Use --via warp, --via direct, or --via block.", hint_type="user")
        cluster, intent = _load()
        exit_ = _resolve_exit(intent, exit_arg)
        try:
            updated, override = add_egress_override_to_intent(
                intent, exit_.id, action=action, values=values, override_id=name
            )
        except ValueError as exc:
            fail(str(exc), hint="Check the exit's overrides with: meridian route list", hint_type="user")
        _apply(cluster, updated, f"Adding route {override.id}")
        if is_json_mode():
            _emit("route.add", RouteMutationResult(exit=exit_.id, override=override), f"Added route {override.id}")
            return
        ok(f"Exit {exit_.id} now sends {_describe(override)} {_verb(override)}")
        if override.action == "warp" and not exit_.warp:
            err_console.print("  [dim]WARP was set up on the exit for this route; other traffic stays direct.[/dim]")


def run_list(exit_arg: str = "") -> None:
    """Show every exit's overrides in first-match order."""
    operation = OperationContext()
    with error_context("route.list", timer=operation.timer):
        _cluster, intent = _load()
        exits = [_resolve_exit(intent, exit_arg)] if exit_arg else list(intent.exits)
        result = build_route_list_result(exits)
        if is_json_mode():
            count = sum(len(entry.overrides) for entry in result.exits)
            emit_json(
                command_envelope(
                    command="route.list",
                    data=result.to_data(),
                    summary=Summary(text=f"{count} route override(s)", changed=False, counts={"routes": count}),
                    timer=operation.timer,
                )
            )
            return
        err_console.print()
        for entry in result.exits:
            default = "WARP" if entry.warp else "direct"
            err_console.print(f"  [bold]{escape(entry.exit)}[/bold]  [dim]default egress: {default}[/dim]")
            for override in entry.overrides:
                err_console.print(f"    {escape(override.id):<16} {escape(_describe(override))} → {override.action}")
            if not entry.overrides:
                err_console.print("    [dim]no overrides[/dim]")
        err_console.print()
        err_console.print("  [dim]Add one:  meridian route add EXIT --via warp netflix.com[/dim]")
        err_console.print()


def run_remove(exit_arg: str, override_id: str, yes: bool = False) -> None:
    """Remove one override from an exit and apply the reviewed topology."""
    from meridian.setup.editor import remove_egress_override_from_intent

    operation = OperationContext()
    with error_context("route.remove", timer=operation.timer):
        if is_json_mode() and not yes:
            fail(
                "Route removal requires explicit confirmation in JSON mode",
                hint="Pass --yes to confirm the mutation.",
                hint_type="user",
            )
        cluster, intent = _load()
        exit_ = _resolve_exit(intent, exit_arg)
        try:
            updated, removed = remove_egress_override_from_intent(intent, exit_.id, override_id)
        except ValueError as exc:
            fail(str(exc), hint="Check the exit's overrides with: meridian route list", hint_type="user")
        default = "WARP" if exit_.warp else "direct"
        if not yes and not confirm(f"Remove route {removed.id}? {_describe(removed)} will go {default} again."):
            raise typer.Exit(1)
        _apply(cluster, updated, f"Removing route {removed.id}")
        if is_json_mode():
            _emit("route.remove", RouteMutationResult(exit=exit_.id, override=removed), f"Removed route {removed.id}")
            return
        ok(f"Removed route {removed.id}; {_describe(removed)} follows the exit's default egress ({default}) again")


def _load() -> tuple[ClusterConfig, SetupIntent]:
    cluster = load_topology_cluster()
    return cluster, require_intent(cluster)


def _resolve_exit(intent: SetupIntent, query: str) -> ExitIntent:
    """Match an exit by ID, saved server ID, saved name, or address."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry

    try:
        entry = ServerRegistry(SERVER_PROFILES_FILE).find(query)
    except LocalStateError as exc:
        fail(exc)
    server_ref = entry.id if entry is not None else query
    for exit_ in intent.exits:
        if query == exit_.id or server_ref == exit_.server_ref:
            return exit_
    fail(f"Exit {query} not found in the topology", hint="Check: meridian node list", hint_type="user")


def _apply(cluster: ClusterConfig, updated: SetupIntent, operation: str) -> None:
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry

    apply_reviewed_intent(
        cluster,
        ServerRegistry(SERVER_PROFILES_FILE),
        updated,
        failure=f"{operation} did not converge",
        hint="The topology change was saved; fix the cause and rerun `meridian apply`.",
    )


def _emit(command: str, result: RouteMutationResult, text: str) -> None:
    emit_json(command_envelope(command=command, data=result.to_data(), summary=Summary(text=text, changed=True)))


def _describe(override: EgressOverrideIntent) -> str:
    prefix = "" if override.match == "domain" else f"{override.match}:"
    return ", ".join(f"{prefix}{value}" for value in override.values)


def _verb(override: EgressOverrideIntent) -> str:
    return {"warp": "through WARP", "direct": "directly", "block": "nowhere (blocked)"}[override.action]
