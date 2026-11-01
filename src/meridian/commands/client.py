"""Client management -- add, show, list, reissue, remove, enable, disable access users.

Limits and expiry (`client set`, `client reset-traffic`) live in `client_limits.py`.

Clients are declared in the topology's access intent; their runtime state lives in the control service.
"""

from __future__ import annotations

from typing import Literal

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import format_traffic, load_topology_cluster, require_intent
from meridian.commands.client_connections import print_connections
from meridian.commands.client_handoff import (
    format_status,
    get_user_or_fail,
    new_client_usernames,
    print_handoff_links,
    resolve_client_mutation_name,
    resolve_client_name,
    validate_client_name,
)
from meridian.commands.client_limits import parse_limit_options, print_client_usage
from meridian.commands.client_schedule import account_expiry
from meridian.commands.client_usage import print_usage
from meridian.commands.client_v4 import run_add_v4, run_remove_v4
from meridian.console import confirm, err_console, error_context, fail, is_json_mode, ok, warn
from meridian.control.client import ControlServiceError
from meridian.control_access import access_panel
from meridian.core.clients import (
    ClientMutationStatus,
    PanelUserLike,
    build_client_reissue_result,
    build_client_status_result,
    client_metadata_paths,
    connection_credential_paths,
)
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.person_metadata import PersonMetadataError, parse_tag_option
from meridian.core.redaction import HANDOFF_LINK_KEYS
from meridian.core.services.clients import ClientNotFoundError, collect_client_list, collect_client_show
from meridian.handoff import client_handoffs, page_url
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def _access_users(cluster: ClusterConfig) -> list[str]:
    """Return the declared access users; panel-wide service users stay hidden."""
    return list(require_intent(cluster).access.users)


def _readable_users(cluster: ClusterConfig) -> list[str] | None:
    """Who list and show may read: on a control service every user is a person of this fleet,
    including those added in its web panel; a Remnawave panel may hold others, so only declared ones."""
    return None if cluster.runs_control_service else _access_users(cluster)


def _console_names(cluster: ClusterConfig) -> list[str]:
    """Every username the control service holds (none on a Remnawave fleet), to resolve a typed name exactly."""
    if not cluster.runs_control_service:
        return []
    try:
        with access_panel(cluster) as panel:
            return [user.username for user in panel.list_users()]
    except RemnawaveError as exc:
        fail(f"Could not read clients: {exc}", hint=exc.hint or "Check panel connectivity", hint_type=exc.category)


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
        usernames, display_names = new_client_usernames(cluster, names)
        run_add_v4(
            usernames,
            cluster,
            operation,
            display_names=display_names,
            collect_handoff=client_handoffs,
            limits=limits,
        )


def run_show(name: str, *, connections: bool = False) -> None:
    """Display connection info for an existing client."""
    operation = OperationContext()
    with error_context("client.show", timer=operation.timer):
        _show(name=name, operation=operation, connections=connections)


def _show(*, name: str, operation: OperationContext, connections: bool = False) -> None:
    cluster = load_topology_cluster()
    name = resolve_client_name(cluster, name, console_people=True, console_names=_console_names(cluster))
    if not cluster.runs_control_service:  # there the name was checked against the service's own rule
        validate_client_name(name)
    access_users = _access_users(cluster)

    def share_url(panel_user: PanelUserLike) -> str:
        return page_url(cluster, panel_user)

    try:
        panel = access_panel(cluster)
        result = collect_client_show(
            panel,
            name,
            build_share_url=share_url,
            build_subscription_url=cluster.panel.subscription_url,
            allowed_usernames=_readable_users(cluster),
            declared_usernames=access_users,
            read_usage=panel.recent_usage if cluster.runs_control_service else None,
            read_connections=panel.user_connections if connections else None,
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
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.show",
                data=result.client.to_data(),
                summary=Summary(text=f"Client {detail.username}", changed=False, counts={"clients": 1}),
                timer=operation.timer,
            ),
            reveal=HANDOFF_LINK_KEYS | client_metadata_paths() | connection_credential_paths(result.client.connections),
        )
        return

    print_handoff_links(result.subscription_url, page_url=result.share_url)
    if result.client.connections is not None:
        print_connections(result.client.connections)

    err_console.print()
    print_client_usage(detail)
    if result.client.usage is not None:
        err_console.print()
        print_usage(result.client.usage)

    err_console.print()
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()


def run_list(*, attention: bool = False, tag: str | None = None) -> None:
    """List the fleet's clients from the control service."""
    operation = OperationContext()
    with error_context("client.list", timer=operation.timer):
        try:
            wanted_tag = parse_tag_option(tag) if tag is not None else None
        except PersonMetadataError:
            fail(t("client.tag_invalid"), hint_type="user")
        _list(operation=operation, attention=attention, tag=wanted_tag)


def _list(*, operation: OperationContext, attention: bool = False, tag: str | None = None) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_topology_cluster()
    try:
        panel = access_panel(cluster)
        result = collect_client_list(
            panel,
            allowed_usernames=_readable_users(cluster),
            declared_usernames=_access_users(cluster),
            read_attention=panel.attention if attention else None,
            tag=tag,
        )
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
            ),
            reveal=client_metadata_paths(result.clients.clients),
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

    access = require_intent(cluster).access
    for client in result.clients.clients:
        shown = access.display_name(client.username)
        label = escape(shown if shown == client.username else f"{shown} ({client.username})")
        row = [
            label if client.declared else f"{label} [dim]· console[/dim]",
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
    suffix = "s" if count != 1 else ""

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {count} client{suffix}[/dim]")
    if not all(client.declared for client in result.clients.clients):
        err_console.print(f"  [dim]{escape(t('client.console_note'))}[/dim]")
    err_console.print()
    err_console.print("  [dim]Add: meridian client add NAME  |  Remove: meridian client remove NAME[/dim]")
    err_console.print()


def run_remove(name: str, yes: bool = False, cut_off: bool = False) -> None:
    """Remove a client from the fleet."""
    operation = OperationContext()
    with error_context("client.remove", timer=operation.timer):
        if is_json_mode() and not yes:
            fail(
                "Client removal requires explicit confirmation in JSON mode",
                hint="Pass --yes to confirm the mutation.",
                hint_type="user",
            )
        cluster = load_topology_cluster()
        name = resolve_client_name(cluster, name)
        validate_client_name(name)
        try:
            run_remove_v4(name, cluster, operation, yes=yes, cut_off=cut_off)
        except ControlServiceError as exc:
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
        access = require_intent(cluster).access
        found = access.find_user(name)
        if found is None:
            known = ", ".join(access.display_name(user) for user in access.users)
            fail(
                f"Client '{name}' is not one of your fleet's clients",
                hint=f"Your clients: {known}" if known else "Add one with: meridian client add NAME",
                hint_type="user",
            )
        name = found
        validate_client_name(name)
        shown = access.display_name(name)
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
                f"Client '{name}' is declared but missing from the panel",
                hint="Run `meridian apply` to restore it, then reissue.",
                hint_type="system",
            )
        try:
            if cut_off:
                reissued, commands = panel.revoke_user_subscription_with_cut_off(user.id)
            else:
                reissued = panel.revoke_user_subscription(user.id)
        except RemnawaveError as exc:
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
                summary=Summary(text=f"Reissued links for client {name}", changed=True, counts={"reissued": 1}),
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
        resolve_client_name(cluster, name, console_people=True)
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
            except RemnawaveError as e:
                fail(
                    f"Could not {action} client '{name}': {e}",
                    hint=e.hint or "Check panel connectivity",
                    hint_type=e.category,
                )

        status = _effective_status(updated, "active" if action == "enable" else "disabled")
        held = status not in {"active", "disabled"}
        if is_json_mode():
            emit_json(
                command_envelope(
                    command=f"client.{action}",
                    data=build_client_status_result(name, status).to_data(),
                    summary=Summary(text=f"{action.capitalize()}d client '{name}'", changed=True),
                    timer=operation.timer,
                )
            )
            return
        ok(f"Client '{name}' {action}d")
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
