"""Client management -- add, show, list, reissue, remove, enable, disable access users.

Limits and expiry (`client set`, `client reset-traffic`) live in `client_limits.py`.

Every person lives in the control service; client commands never edit topology.
"""

from __future__ import annotations

from typing import Literal

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import format_traffic
from meridian.commands._helpers import load_people_cluster as load_topology_cluster
from meridian.commands.client_connections import print_connections
from meridian.commands.client_handoff import (
    format_status,
    get_user_or_fail,
    print_handoff_links,
    resolve_client_mutation_name,
    resolve_client_name,
)
from meridian.commands.client_limits import parse_limit_options, print_client_usage
from meridian.commands.client_schedule import account_expiry
from meridian.commands.client_usage import print_usage
from meridian.commands.client_v4 import run_add_v4, run_remove_v4
from meridian.console import confirm, err_console, error_context, fail, is_json_mode, ok, warn
from meridian.control.client import ControlServiceError
from meridian.control_access import access_panel
from meridian.core.clients import (
    ClientListResult,
    ClientMutationStatus,
    PanelUserLike,
    build_client_list_result,
    build_client_reissue_result,
    build_client_status_result,
    client_metadata_paths,
    connection_credential_paths,
)
from meridian.core.errors import MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_query import PeopleQuery
from meridian.core.person_metadata import PersonMetadataError, parse_tag_option
from meridian.core.redaction import HANDOFF_LINK_KEYS
from meridian.core.services.clients import ClientListServiceResult, ClientNotFoundError, collect_client_show
from meridian.handoff import page_url
from meridian.i18n import t
from meridian.renderers import emit_json


def run_add(
    names: list[str],
    *,
    limit: str | None = None,
    reset: str | None = None,
    expires: str | None = None,
    devices: str | None = None,
    reset_day: str | None = None,
    duration: str | None = None,
    renew_days: str | None = None,
    renew_months: str | None = None,
    renewals: str | None = None,
    no_renew: bool = False,
) -> None:
    """Add one or more clients to the fleet, optionally with traffic limits and an expiry."""
    operation = OperationContext()
    with error_context("client.add", timer=operation.timer):
        limits = parse_limit_options(
            limit=limit,
            reset=reset,
            expires=expires,
            devices=devices,
            reset_day=reset_day,
            duration=duration,
            renew_days=renew_days,
            renew_months=renew_months,
            renewals=renewals,
            no_renew=no_renew,
        )
        try:
            limits.for_creation()
        except ValidationError:
            fail(t("lifecycle.invalid_creation"), hint_type="user")
        seen: set[str] = set()
        for name in names:
            if name.strip().casefold() in seen:
                fail(
                    f"Duplicate client name in batch: '{name}'",
                    hint="Remove the duplicate and try again",
                    hint_type="user",
                )
            seen.add(name.strip().casefold())
        cluster = load_topology_cluster()
        try:
            run_add_v4(names, cluster, operation, limits=limits)
        except MeridianError as exc:
            fail(exc)


def run_show(name: str, *, connections: bool = False) -> None:
    """Display connection info for an existing client."""
    operation = OperationContext()
    with error_context("client.show", timer=operation.timer):
        _show(name=name, operation=operation, connections=connections)


def _show(*, name: str, operation: OperationContext, connections: bool = False) -> None:
    cluster = load_topology_cluster(read_only=True)
    name = resolve_client_name(cluster, name)

    def share_url(panel_user: PanelUserLike) -> str:
        return page_url(cluster, panel_user)

    try:
        panel = access_panel(cluster, read_only=True)
        result = collect_client_show(
            panel,
            name,
            build_share_url=share_url,
            build_subscription_url=cluster.panel.subscription_url,
            read_usage=panel.recent_usage if cluster.runs_control_service else None,
            read_connections=panel.user_connections if connections else None,
            read_access=panel.user_access if cluster.runs_control_service else None,
            read_default_access=bool(cluster.topology_intent and cluster.topology_intent.access_profiles),
        )
    except ClientNotFoundError:
        fail(
            f"Client '{name}' not found",
            hint="Check client name with: meridian client list",
            hint_type="user",
        )
    except MeridianError as e:
        fail(
            f"Could not show client: {e}",
            hint=e.hint or "Check panel connectivity",
            hint_type=e.category,
        )

    detail = result.client.client
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.show",
                data=result.client.to_data(),
                summary=Summary(
                    text=f"Client {detail.display_name or detail.username}", changed=False, counts={"clients": 1}
                ),
                timer=operation.timer,
            ),
            reveal=HANDOFF_LINK_KEYS | client_metadata_paths() | connection_credential_paths(result.client.connections),
        )
        return

    shown = detail.display_name or detail.username
    label = shown if shown == detail.username else f"{shown} ({detail.username})"
    err_console.print(f"  [bold]{escape(label)}[/bold]")
    print_handoff_links(result.subscription_url, page_url=result.share_url)
    if result.client.connections is not None:
        print_connections(result.client.connections)
    if result.client.access is not None:
        for connection in result.client.access.connections:
            err_console.print(
                "  "
                + escape(connection.remark)
                + " — "
                + escape(t("profiles.granted_by", profiles=", ".join(connection.granted_by)))
            )

    err_console.print()
    print_client_usage(detail)
    if result.client.usage is not None:
        err_console.print()
        print_usage(result.client.usage)

    err_console.print()
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()


def run_list(*, attention: bool = False, tag: str | None = None, query: PeopleQuery | None = None) -> None:
    """List the fleet's clients from the control service."""
    operation = OperationContext()
    with error_context("client.list", timer=operation.timer):
        try:
            wanted_tag = parse_tag_option(tag) if tag is not None else None
        except PersonMetadataError:
            fail(t("client.tag_invalid"), hint_type="user")
        _list(
            operation=operation,
            query=query
            or PeopleQuery(
                attention="all" if attention else "",
                tag=("none" if wanted_tag == "" else wanted_tag) if wanted_tag is not None else "",
            ),
        )


def _list(*, operation: OperationContext, query: PeopleQuery) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_topology_cluster(read_only=True)
    try:
        with access_panel(cluster, read_only=True) as panel:
            page, users = panel.query_users(query, include_metadata=is_json_mode())
        listed = build_client_list_result(
            users,
            attention={person.username: person.attention_reasons for person in page.users} if query.attention else None,
        )
        listed = ClientListResult.model_validate(
            {**listed.model_dump(), "total": page.total, "next_cursor": page.next_cursor, "facets": page.facets}
        )
        result = ClientListServiceResult(clients=listed)
        attention = bool(query.attention)
    except MeridianError as e:
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
            ),
            reveal=client_metadata_paths(result.clients.clients) | {"data.facets"},
        )
        return

    table = Table(
        title=t("attention.title") if attention else "Proxy Clients",
        show_lines=False,
        pad_edge=False,
        box=ROUNDED,
        padding=(0, 1),
    )
    table.add_column("Name", style="bold cyan")
    table.add_column(t("client.label.tag"), style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Traffic", style="dim")
    table.add_column("Expires", style="dim")
    table.add_column("Last seen", style="dim")
    if attention:
        table.add_column(t("attention.label.reason"), no_wrap=True)

    names = {person.username: person.display_name for person in page.users}
    for client in result.clients.clients:
        shown = names.get(client.username, client.username)
        label = escape(shown if shown == client.username else f"{shown} ({client.username})")
        row = [
            label,
            escape(client.tag or "-"),
            format_status(client.status),
            format_traffic(client.traffic_used_bytes, client.traffic_limit_bytes),
            escape(account_expiry(client) if client.expires_at or client.activation_duration_days is not None else "-"),
            escape(client.last_seen or "-"),
        ]
        if attention:
            row.append(escape(", ".join(t(f"attention.reason.{reason}") for reason in client.attention_reasons)))
        table.add_row(*row)

    count = result.clients.summary.clients
    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]{escape(t('bulk.list_count', shown=count, total=page.total))}[/dim]")
    if page.next_cursor:
        err_console.print(f"  [dim]--cursor {escape(page.next_cursor)}[/dim]")
    err_console.print()
    err_console.print("  [dim]Add: meridian client add NAME  |  Remove: meridian client remove NAME[/dim]")
    err_console.print()


def run_remove(
    name: str | list[str] | None = None,
    yes: bool = False,
    cut_off: bool = False,
    *,
    proposal: str | None = None,
    dry_run: bool = False,
) -> None:
    """Remove a client from the fleet."""
    operation = OperationContext()
    with error_context("client.remove", timer=operation.timer):
        if is_json_mode() and not yes and not dry_run:
            fail(
                "Client removal requires explicit confirmation in JSON mode",
                hint="Pass --yes to confirm the mutation.",
                hint_type="user",
            )
        try:
            names = [name] if isinstance(name, str) else name or []
            if proposal is not None:
                fail("--proposal has been retired. Use `meridian client remove NAME...`.", hint_type="user")
            if not names:
                fail("Give one or more client names.", hint_type="user")
            if cut_off and (len(names) != 1 or dry_run):
                fail("--cut-off is available for one person without --dry-run.", hint_type="user")
            cluster = load_topology_cluster()
            with access_panel(cluster) as panel:
                names = [resolve_client_mutation_name(cluster, panel, value) for value in names]
            if len(names) != len(set(names)):
                fail("List each person once, even when using display names.", hint_type="user")
            if len(names) == 1 and not dry_run:
                run_remove_v4(names[0], cluster, operation, yes=yes, cut_off=cut_off)
            else:
                from meridian.commands.client_removal import run_reviewed_removal

                run_reviewed_removal(names, cluster, operation, yes=yes, dry_run=dry_run)
        except MeridianError as exc:
            fail(exc)


def run_reissue(name: str, yes: bool = False, cut_off: bool = False) -> None:
    """Replace a client's subscription and connection page; the old ones stop working."""
    operation = OperationContext()
    with error_context("client.reissue", timer=operation.timer):
        if is_json_mode() and not yes:
            fail(
                "Reissuing client links requires explicit confirmation in JSON mode",
                hint="Pass --yes to confirm the mutation.",
                hint_type="user",
            )
        cluster = load_topology_cluster()
        with access_panel(cluster) as panel:
            try:
                panel.require_people_service()
            except MeridianError as exc:
                fail(exc)
            name = resolve_client_mutation_name(cluster, panel, name)
            person = get_user_or_fail(panel, name, action="load")
            assert person is not None
            shown = person.display_name or name
        from meridian.commands.node_command_results import cut_off_disclosure
        from meridian.node_commands import cut_off_scope

        try:
            scope = cut_off_scope(cluster, name) if cut_off else None
        except ControlServiceError as exc:
            fail(exc)
        confirmation = t("client.reissue_confirm", name=shown)
        if scope is not None:
            confirmation += "\n\n" + cut_off_disclosure(scope)
            if yes and not is_json_mode():
                warn(cut_off_disclosure(scope))
        if not yes and not confirm(confirmation):
            raise typer.Exit(1)
        try:
            _reissue(cluster, name, shown, operation, cut_off=cut_off)
        except ControlServiceError as exc:
            fail(exc)


def _reissue(
    cluster: ClusterConfig, name: str, shown: str, operation: OperationContext, *, cut_off: bool = False
) -> None:
    from meridian.commands.node_command_results import print_command_results
    from meridian.control.command_models import NodeCommandRecord
    from meridian.node_commands import command_exit_code, command_warnings, wait_commands

    commands: list[NodeCommandRecord] = []
    with access_panel(cluster) as panel:
        user = get_user_or_fail(panel, name, action="load")
        if user is None:
            fail(
                f"Client '{name}' not found",
                hint="Check names with: meridian client list",
                hint_type="system",
            )
        try:
            if cut_off:
                reissued, commands = panel.revoke_user_subscription_with_cut_off(user.id)
            else:
                reissued = panel.revoke_user_subscription(user.id)
        except MeridianError as exc:
            fail(
                f"Could not reissue client '{name}': {exc}",
                hint=(exc.hint or "Check panel connectivity") + f"; then rerun `meridian client reissue {name}`.",
                hint_type=exc.category,
            )

    subscription_url = cluster.panel.subscription_url(reissued.short_uuid)
    cut_off_results = wait_commands(cluster, commands) if cut_off else None
    warnings = command_warnings(cut_off_results or [])
    exit_code = command_exit_code(cut_off_results or [])

    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.reissue",
                data=build_client_reissue_result(
                    reissued, page_url=subscription_url, subscription_url=subscription_url, cut_off=cut_off_results
                ).to_data(),
                summary=Summary(text=f"Reissued links for client {shown}", changed=True, counts={"reissued": 1}),
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            ),
            reveal=HANDOFF_LINK_KEYS,
        )
        if exit_code:
            raise typer.Exit(exit_code)
        return

    print_command_results(cut_off_results or [])
    ok(t("client.reissued", name=shown))
    print_handoff_links(subscription_url, page_url=subscription_url)
    err_console.print()
    err_console.print(f"  [dim]{escape(t('client.reissue_send', name=shown))}[/dim]")
    err_console.print()
    if exit_code:
        raise typer.Exit(exit_code)


def _effective_status(updated: PanelUserLike | None, requested: ClientMutationStatus) -> ClientMutationStatus:
    """The status the directory reports after the change (without an answer, the request stands)."""
    if updated is None:
        return requested
    statuses: dict[str, ClientMutationStatus] = {
        "active": "active",
        "disabled": "disabled",
        "expired": "expired",
        "limited": "limited",
    }
    return statuses.get(updated.status.lower(), requested)


def run_enable(name: str) -> None:
    """Re-enable a suspended client so they can connect again."""
    _set_enabled(name, "enable")


def run_disable(name: str) -> None:
    """Temporarily suspend a client — they cannot connect until re-enabled."""
    _set_enabled(name, "disable")


def _set_enabled(name: str, action: Literal["enable", "disable"]) -> None:
    operation = OperationContext()
    with error_context(f"client.{action}", timer=operation.timer):
        cluster = load_topology_cluster()
        with access_panel(cluster) as panel:
            name = resolve_client_mutation_name(cluster, panel, name)
            client = get_user_or_fail(panel, name, action="load")
            if client is None:
                fail(
                    f"Client '{name}' not found",
                    hint="Check client name with: meridian client list",
                    hint_type="user",
                )
            try:
                updated = (panel.enable_user if action == "enable" else panel.disable_user)(client.id)
            except MeridianError as e:
                fail(
                    f"Could not {action} client '{name}': {e}",
                    hint=e.hint or "Check panel connectivity",
                    hint_type=e.category,
                )

        shown = client.display_name or name
        status = _effective_status(updated, "active" if action == "enable" else "disabled")
        held = status not in {"active", "disabled"}
        if is_json_mode():
            emit_json(
                command_envelope(
                    command=f"client.{action}",
                    data=build_client_status_result(name, status, display_name=client.display_name).to_data(),
                    summary=Summary(text=f"{action.capitalize()}d client '{shown}'", changed=True),
                    timer=operation.timer,
                )
            )
            return
        ok(f"Client '{shown}' {action}d")
        if held:
            if status == "expired":
                warn(f"Client '{name}' is still expired: its expiry has passed")
                lift = f"meridian client set {escape(name)} --expires never"
            else:
                warn(f"Client '{name}' is still limited: it used its traffic limit")
                lift = f"meridian client reset-traffic {escape(name)}"
            err_console.print(f"  [dim]Lift it:           {lift}[/dim]")
        err_console.print()
        if action == "enable":
            err_console.print(f"  [dim]Show client:       meridian client show {escape(name)}[/dim]")
        else:
            err_console.print(f"  [dim]Re-enable:         meridian client enable {escape(name)}[/dim]")
        err_console.print("  [dim]View all clients:  meridian client list[/dim]")
        err_console.print()
