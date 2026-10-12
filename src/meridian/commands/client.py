"""Client management -- add, show, list, remove proxy clients.

All client state lives in Remnawave's database. No local proxy.yml,
no credential sync, no rollback. One API call per operation.
"""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import format_traffic, load_topology_cluster, make_panel
from meridian.commands.client_handoff import (
    format_status as _format_status,
)
from meridian.commands.client_handoff import (
    get_user_or_fail as _get_user_or_fail,
)
from meridian.commands.client_handoff import (
    print_handoff_links as _print_handoff_links,
)
from meridian.commands.client_handoff import (
    resolve_page_url as _resolve_page_url,
)
from meridian.commands.client_handoff import (
    validate_client_name as _validate_client_name,
)
from meridian.commands.client_state import (
    build_client_persistence_warning as _build_client_persistence_warning,
)
from meridian.commands.client_state import (
    require_access_user as _require_access_user,
)
from meridian.commands.client_v4 import run_add_v4 as _run_add_v4
from meridian.commands.client_v4 import run_remove_v4 as _run_remove_v4
from meridian.console import err_console, error_context, fail, is_json_mode, ok, warn
from meridian.core.clients import (
    PanelUserLike,
    build_client_status_result,
)
from meridian.core.fleet import public_url
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.services.clients import ClientNotFoundError, collect_client_list, collect_client_show
from meridian.handoff import publish_connection_pages as _publish_handoff
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def _access_users(cluster: ClusterConfig) -> list[str]:
    """Return the declared access users; panel-wide service users stay hidden."""
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    return list(intent.access.users)


# -- Client Add --


def run_add(
    names: list[str],
    json_mode: bool = False,
) -> None:
    """Add one or more clients to the proxy cluster."""
    operation = OperationContext()
    with error_context("client.add", timer=operation.timer):
        _run_add(names=names, operation=operation, json_mode=json_mode)


def _run_add(
    *,
    names: list[str],
    operation: OperationContext,
    json_mode: bool = False,
) -> None:
    """Implementation for client add with command metadata attached."""
    # --- Validate ALL names first (fail early) ---
    for name in names:
        _validate_client_name(name)

    # Reject duplicates within the batch
    seen: set[str] = set()
    for name in names:
        lower = name.lower()
        if lower in seen:
            fail(
                f"Duplicate client name in batch: '{name}'",
                hint="Remove the duplicate and try again",
                hint_type="user",
            )
        seen.add(lower)

    _run_add_v4(names, load_topology_cluster(), operation, publish_handoff=_publish_handoff)


# -- Client Show --


def run_show(
    name: str,
    repair_page: bool = False,
) -> None:
    """Display connection info for an existing client."""
    operation = OperationContext()
    with error_context("client.show", timer=operation.timer):
        _run_show(name=name, repair_page=repair_page, operation=operation)


def _run_show(
    *,
    name: str,
    repair_page: bool = False,
    operation: OperationContext,
) -> None:
    """Implementation for client show with command metadata attached."""
    _validate_client_name(name)
    cluster = load_topology_cluster()
    access_users = _access_users(cluster)
    repaired = False
    repair_persistence_error = ""

    def share_url(panel_user: PanelUserLike) -> str:
        nonlocal repair_persistence_error, repaired
        resolution = _resolve_page_url(cluster, panel_user, repair=repair_page)
        repaired = repaired or resolution.repaired
        repair_persistence_error = resolution.persistence_error or repair_persistence_error
        return resolution.url

    try:
        result = collect_client_show(
            make_panel(cluster),
            name,
            build_share_url=share_url,
            build_subscription_url=cluster.panel.subscription_url,
            allowed_usernames=access_users,
        )
    except ClientNotFoundError:
        fail(
            f"Client '{name}' not found",
            hint="Check client name with: meridian client list",
            hint_type="user",
        )
    except RemnawaveError as e:
        fail(
            f"Could not show client: {e}",
            hint=e.hint or "Check panel connectivity",
            hint_type=e.category,
        )

    detail = result.client.client
    repair_warnings: list[MeridianError] = []
    if repair_page and not result.share_url:
        repair_warnings.append(
            MeridianError(
                code="MERIDIAN_CLIENT_PAGE_REPAIR_FAILED",
                category="system",
                message=f"Client '{detail.username}' is available, but its connection page could not be repaired.",
                hint="Check panel-host SSH and page deployment state, then retry --repair-page.",
                retryable=True,
                exit_code=3,
                details={"client": detail.username},
            )
        )
    if repair_persistence_error:
        repair_warnings.append(
            _build_client_persistence_warning(
                operation="recording connection-page evidence for client",
                client=detail.username,
                reason=repair_persistence_error,
                remote_state_changed=repaired or repair_page,
            )
        )
    exit_code = 3 if repair_warnings else 0
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.show",
                data=result.client.to_data(),
                summary=Summary(text=f"Client {detail.username}", changed=repaired, counts={"clients": 1}),
                exit_code=exit_code,
                warnings=repair_warnings,
                timer=operation.timer,
            )
        )
        if exit_code:
            raise typer.Exit(exit_code)
        return

    # Print connection page URL + subscription
    _print_handoff_links(result.subscription_url, page_url=result.share_url)

    # Print traffic stats
    err_console.print()
    err_console.print(f"  [bold]Status[/bold]    {_format_status(detail.status)}")
    traffic = format_traffic(detail.traffic_used_bytes, detail.traffic_limit_bytes)
    err_console.print(f"  [bold]Traffic[/bold]   {traffic}")
    if detail.last_seen:
        err_console.print(f"  [bold]Last seen[/bold] {escape(detail.last_seen)}")

    err_console.print()
    if cluster.panel.url:
        err_console.print(f"  [dim]Remnawave panel:   {escape(public_url(cluster.panel.display_url))}[/dim]")
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
    if repair_warnings:
        warn(repair_warnings[0].message)
        raise typer.Exit(3)


def run_list() -> None:
    """List all clients from the Remnawave panel."""
    operation = OperationContext()
    with error_context("client.list", timer=operation.timer):
        _run_list(operation=operation)


def _run_list(
    *,
    operation: OperationContext,
) -> None:
    """Implementation for client list with command metadata attached."""
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_topology_cluster()
    try:
        result = collect_client_list(make_panel(cluster), allowed_usernames=_access_users(cluster))
    except RemnawaveError as e:
        fail(
            f"Could not list clients: {e}",
            hint=e.hint or "Check panel connectivity",
            hint_type=e.category,
        )

    if is_json_mode():
        data = result.clients.to_data()
        emit_json(
            command_envelope(
                command="client.list",
                data=data,
                summary=Summary(
                    text=result.clients.summary.text,
                    changed=False,
                    counts=result.clients.summary.model_dump(),
                ),
                timer=operation.timer,
            )
        )
        return

    table = Table(
        title="Proxy Clients",
        show_lines=False,
        pad_edge=False,
        box=ROUNDED,
        padding=(0, 2),
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Traffic", style="dim")
    table.add_column("Last seen", style="dim")

    for client in result.clients.clients:
        table.add_row(
            escape(client.username),
            _format_status(client.status),
            format_traffic(client.traffic_used_bytes, client.traffic_limit_bytes),
            escape(client.last_seen or "-"),
        )

    count = result.clients.summary.clients
    suffix = "s" if count != 1 else ""

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {count} client{suffix}[/dim]")
    err_console.print()
    err_console.print("  [dim]Add: meridian client add NAME  |  Remove: meridian client remove NAME[/dim]")
    err_console.print()


# -- Client Remove --


def run_remove(
    name: str,
    yes: bool = False,
    json_mode: bool = False,
) -> None:
    """Remove a client from the proxy cluster."""
    operation = OperationContext()
    with error_context("client.remove", timer=operation.timer):
        _run_remove(name=name, yes=yes, operation=operation)


def _run_remove(
    *,
    name: str,
    yes: bool = False,
    operation: OperationContext,
) -> None:
    """Implementation for client remove with command metadata attached."""
    _validate_client_name(name)
    if is_json_mode() and not yes:
        fail(
            "Client removal requires explicit confirmation in JSON mode",
            hint="Pass --yes to confirm the mutation.",
            hint_type="user",
        )
    _run_remove_v4(name, load_topology_cluster(), operation, yes=yes)


# -- Client Enable --


def run_enable(
    name: str,
    json_mode: bool = False,
) -> None:
    """Re-enable a suspended client so they can connect again."""
    operation = OperationContext()
    with error_context("client.enable", timer=operation.timer):
        _run_enable(name=name, operation=operation)


def _run_enable(
    *,
    name: str,
    operation: OperationContext,
) -> None:
    """Implementation for client enable with command metadata attached."""
    _validate_client_name(name)
    cluster = load_topology_cluster()
    _require_access_user(cluster, name)
    panel = make_panel(cluster)

    with panel:
        client = _get_user_or_fail(panel, name, action="load")
        if client is None:
            fail(
                f"Client '{name}' not found",
                hint="Check client name with: meridian client list",
                hint_type="user",
            )

        try:
            panel.enable_user(client.id)
        except RemnawaveError as e:
            fail(
                f"Could not enable client '{name}': {e}",
                hint=e.hint or "Check panel connectivity",
                hint_type=e.category,
            )

        if is_json_mode():
            result = build_client_status_result(name, "active")
            emit_json(
                command_envelope(
                    command="client.enable",
                    data=result.to_data(),
                    summary=Summary(text=f"Enabled client '{name}'", changed=True),
                    timer=operation.timer,
                )
            )
            return

        ok(f"Client '{name}' enabled")

    err_console.print()
    err_console.print("  [dim]Show client:       meridian client show " + escape(name) + "[/dim]")
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()


# -- Client Disable --


def run_disable(
    name: str,
    json_mode: bool = False,
) -> None:
    """Temporarily suspend a client — they cannot connect until re-enabled."""
    operation = OperationContext()
    with error_context("client.disable", timer=operation.timer):
        _run_disable(name=name, operation=operation)


def _run_disable(
    *,
    name: str,
    operation: OperationContext,
) -> None:
    """Implementation for client disable with command metadata attached."""
    _validate_client_name(name)
    cluster = load_topology_cluster()
    _require_access_user(cluster, name)
    panel = make_panel(cluster)

    with panel:
        client = _get_user_or_fail(panel, name, action="load")
        if client is None:
            fail(
                f"Client '{name}' not found",
                hint="Check client name with: meridian client list",
                hint_type="user",
            )

        try:
            panel.disable_user(client.id)
        except RemnawaveError as e:
            fail(
                f"Could not disable client '{name}': {e}",
                hint=e.hint or "Check panel connectivity",
                hint_type=e.category,
            )

        if is_json_mode():
            result = build_client_status_result(name, "disabled")
            warnings = [
                MeridianError(
                    code="MERIDIAN_V4_CLIENT_DISABLE_TEMPORARY",
                    category="user",
                    message=f"Client '{name}' is disabled only until the next V4 apply.",
                    hint="V4 access intent declares managed users active.",
                    exit_code=0,
                )
            ]
            emit_json(
                command_envelope(
                    command="client.disable",
                    data=result.to_data(),
                    summary=Summary(text=f"Temporarily disabled V4 client '{name}'", changed=True),
                    warnings=warnings,
                    timer=operation.timer,
                )
            )
            return

        ok(f"Client '{name}' disabled")
        warn("This V4 client is disabled only until the next `meridian apply`")

    err_console.print()
    err_console.print("  [dim]Re-enable:         meridian client enable " + escape(name) + "[/dim]")
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
