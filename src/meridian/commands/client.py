"""Client management -- add, show, list, remove proxy clients.

All client state lives in Remnawave's database. No local proxy.yml,
no credential sync, no rollback. One API call per operation.
"""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.commands._helpers import format_traffic, load_cluster, make_panel
from meridian.commands.client_handoff import (
    build_page_url as _build_page_url,
)
from meridian.commands.client_handoff import (
    cleanup_client_page as _cleanup_client_page,
)
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
    print_subscription as _print_subscription,
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
    capture_client_state_failure as _capture_client_state_failure,
)
from meridian.commands.client_state import (
    persist_client_state as _persist_client_state,
)
from meridian.commands.client_state import (
    require_v4_access_user as _require_v4_access_user,
)
from meridian.commands.client_v4 import run_add_v4 as _run_add_v4
from meridian.console import confirm, err_console, error_context, fail, info, is_json_mode, ok, warn
from meridian.core.clients import (
    PanelUserLike,
    build_client_add_result,
    build_client_remove_result,
    build_client_status_result,
)
from meridian.core.fleet import public_url
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.redaction import redact_string
from meridian.core.services.clients import ClientNotFoundError, collect_client_list, collect_client_show
from meridian.remnawave import RemnawaveError, User
from meridian.renderers import emit_json
from meridian.ssh import SSHError

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

    cluster = load_cluster()
    if cluster.topology_intent is not None:
        _run_add_v4(names, cluster, operation, print_subscription=_print_subscription)
        return
    panel = make_panel(cluster)

    with panel:
        # Check for conflicts with existing users
        for name in names:
            existing = _get_user_or_fail(panel, name, action="check")
            if existing is not None:
                fail(
                    f"Client '{name}' already exists",
                    hint="Use: meridian client show " + name,
                    hint_type="user",
                )

        # --- Best-effort creation ---
        succeeded: list[tuple[str, User]] = []
        failed: list[tuple[str, str]] = []

        squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
        for name in names:
            info(f"Adding client '{name}'...")
            try:
                new_user = panel.create_user(name, squad_uuids=squad_uuids)
            except RemnawaveError as e:
                failed.append((name, str(e)))
                continue
            succeeded.append((name, new_user))

        # If everything failed, exit with the first error
        if not succeeded:
            first_name, first_err = failed[0]
            fail(
                f"Could not create client '{first_name}': {first_err}",
                hint="Check panel connectivity",
                hint_type="system",
            )

        # Hybrid sync + page deploy for each successful client
        from meridian.reconciler.snapshots import hybrid_sync_desired_clients_add

        # Open one SSH connection for all page deployments
        _conn = None
        panel_node = cluster.panel_node
        deploy_pages = bool(cluster.panel.server_ip and panel_node)
        deployed_page_urls: dict[str, str] = {}
        page_failures: list[tuple[str, str]] = []
        page_state_changed = False
        persistence_error = ""

        if deploy_pages:
            try:
                from meridian.ssh import ServerConnection

                _conn = ServerConnection(
                    cluster.panel.server_ip,
                    user=cluster.panel.ssh_user or "root",
                    port=getattr(cluster.panel, "ssh_port", 22) or 22,
                )
                _conn.__enter__()
            except (OSError, RuntimeError, SSHError):
                _conn = None  # Non-fatal

        try:
            for name, new_user in succeeded:
                state_error = _capture_client_state_failure(lambda: hybrid_sync_desired_clients_add(cluster, name))
                persistence_error = state_error or persistence_error

                page_url = _build_page_url(cluster, new_user.vless_uuid, require_deployed=False)

                # Deploy connection page files
                if deploy_pages and _conn is not None and page_url and panel_node:
                    try:
                        from meridian.pwa import deploy_client_page, mark_connection_page_failed

                        sub_url = panel.get_subscription_url(new_user.short_uuid) if new_user.short_uuid else ""
                        deployed_url = deploy_client_page(
                            _conn,
                            cluster,
                            panel_node,
                            new_user.vless_uuid,
                            name,
                            sub_url,
                        )
                        if deployed_url:
                            deployed_page_urls[name] = deployed_url
                            page_state_changed = True
                        else:
                            mark_connection_page_failed(cluster, new_user.vless_uuid)
                            page_state_changed = True
                            page_failures.append((name, "page upload did not complete"))
                    except (OSError, RuntimeError, SSHError, RemnawaveError) as exc:
                        from meridian.pwa import mark_connection_page_failed

                        mark_connection_page_failed(cluster, new_user.vless_uuid)
                        page_state_changed = True
                        page_failures.append((name, redact_string(str(exc))))
                elif page_url:
                    from meridian.pwa import mark_connection_page_failed

                    mark_connection_page_failed(cluster, new_user.vless_uuid)
                    page_state_changed = True
                    page_failures.append((name, "panel SSH connection is unavailable"))
        finally:
            if _conn is not None:
                try:
                    _conn.__exit__(None, None, None)
                except (OSError, RuntimeError, SSHError):
                    pass
        if page_state_changed or persistence_error:
            persistence_error = _persist_client_state(cluster)

        # --- JSON output ---
        partial_warnings = [
            MeridianError(
                code="MERIDIAN_CLIENT_ADD_PARTIAL_FAILURE",
                category="system",
                message=f"Could not add client '{name}': {redact_string(reason)}",
                hint="Retry the failed client after checking panel connectivity.",
                retryable=True,
                exit_code=3,
                details={"client": name},
            )
            for name, reason in failed
        ]
        partial_warnings.extend(
            MeridianError(
                code="MERIDIAN_CLIENT_PAGE_DEPLOY_FAILED",
                category="system",
                message=f"Client '{name}' was added, but its share page was not deployed: {reason}",
                hint=(
                    f"Use the subscription URL now. After restoring panel SSH, run "
                    f"`meridian client show {name} --repair-page`."
                ),
                retryable=True,
                exit_code=3,
                details={"client": name},
            )
            for name, reason in page_failures
        )
        if persistence_error:
            partial_warnings.append(
                _build_client_persistence_warning(
                    operation="adding clients",
                    reason=persistence_error,
                )
            )
        if is_json_mode():
            result = build_client_add_result(succeeded)
            count = len(succeeded)
            exit_code = 3 if failed or page_failures or persistence_error else 0
            counts = {
                "added": count,
                "failed": len(failed),
                "page_deploy_failed": len(page_failures),
            }
            if persistence_error:
                counts["state_save_failed"] = 1
            emit_json(
                command_envelope(
                    command="client.add",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"Added {count} client(s); {len(failed)} failed",
                        changed=True,
                        counts=counts,
                    ),
                    exit_code=exit_code,
                    warnings=partial_warnings,
                    timer=operation.timer,
                )
            )
            if exit_code:
                raise typer.Exit(exit_code)
            return

        # --- Report results ---
        succeeded_names = [n for n, _ in succeeded]
        if failed:
            ok(f"Added {len(succeeded)} client(s): {', '.join(succeeded_names)}")
            for fail_name, fail_err in failed:
                err_console.print(
                    f"  [red]Failed to add '{escape(fail_name)}': {escape(redact_string(fail_err))}[/red]"
                )
        else:
            ok(f"Added {len(succeeded)} client(s): {', '.join(succeeded_names)}")
        for page_name, page_error in page_failures:
            warn(f"Share page for '{page_name}' was not deployed: {page_error}")
        if persistence_error:
            warn(partial_warnings[-1].message)

        # Show subscription info for each added client
        handoff_failures: list[tuple[str, str]] = []
        for name, new_user in succeeded:
            page_url = deployed_page_urls.get(name, "")
            if len(succeeded) > 1:
                err_console.print()
                err_console.print(f"  [bold]— {escape(name)} —[/bold]")
            handoff_error = _print_subscription(panel, new_user, page_url=page_url)
            if isinstance(handoff_error, str) and handoff_error:
                handoff_failures.append((name, handoff_error))
                warn(f"Client '{name}' was added, but its subscription handoff is unavailable: {handoff_error}")

    err_console.print()
    if len(succeeded) == 1:
        only_name = succeeded[0][0]
        err_console.print("  [dim]Show client:       meridian client show " + escape(only_name) + "[/dim]")
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
    if failed or page_failures or handoff_failures or persistence_error:
        raise typer.Exit(3)


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
    cluster = load_cluster()
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
            allowed_usernames=(cluster.topology_intent.access.users if cluster.topology_intent is not None else None),
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

    cluster = load_cluster()
    try:
        result = collect_client_list(
            make_panel(cluster),
            allowed_usernames=(cluster.topology_intent.access.users if cluster.topology_intent is not None else None),
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
    cluster = load_cluster()
    if cluster.topology_intent is not None:
        fail(
            f"Client '{name}' is managed by V4 access intent",
            hint=(
                "Safe V4 access-user retirement is not available yet. Use `meridian client disable` for an "
                "immediate temporary revocation; do not delete the panel user directly."
            ),
            hint_type="user",
        )
    panel = make_panel(cluster)

    with panel:
        client = _get_user_or_fail(panel, name, action="load")
        if client is None:
            fail(
                f"Client '{name}' not found",
                hint="Check client name with: meridian client list",
                hint_type="user",
            )

        if not yes and not confirm(f"Remove client '{name}'?"):
            raise typer.Exit(1)

        try:
            success = panel.delete_user(client.uuid)
        except RemnawaveError as exc:
            fail(
                f"Could not remove client '{name}': {exc}",
                hint=exc.hint or "Check panel connectivity",
                hint_type=exc.category,
            )
        if not success:
            fail(
                f"Could not remove client '{name}'",
                hint="The panel may be unreachable. Try again.",
                hint_type="system",
            )

        # Hybrid sync — drop from desired_clients (if managed declaratively)
        # so the next `meridian apply` does not re-create the just-removed user.
        from meridian.reconciler.snapshots import hybrid_sync_desired_clients_remove

        persistence_error = _capture_client_state_failure(lambda: hybrid_sync_desired_clients_remove(cluster, name))

        page_cleanup = _cleanup_client_page(client, cluster)
        if page_cleanup.state_changed or persistence_error:
            persistence_error = _persist_client_state(cluster)
        partial_warnings: list[MeridianError] = []
        if persistence_error:
            partial_warnings.append(
                _build_client_persistence_warning(
                    operation="removing client",
                    client=name,
                    reason=persistence_error,
                )
            )
        if page_cleanup.error:
            partial_warnings.append(
                MeridianError(
                    code="MERIDIAN_CLIENT_PAGE_CLEANUP_FAILED",
                    category="system",
                    message=(
                        f"Client '{name}' was removed from the panel, but legacy connection-page cleanup "
                        f"could not be confirmed: {page_cleanup.error}"
                    ),
                    hint="Restore panel-host SSH access, remove the stale page, then verify local page evidence.",
                    retryable=True,
                    exit_code=3,
                    details={"client": name, "remote_state_changed": True},
                )
            )

        result = build_client_remove_result(name)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="client.remove",
                    data=result.to_data(),
                    summary=Summary(text=f"Removed client '{name}'", changed=True),
                    exit_code=3 if partial_warnings else 0,
                    warnings=partial_warnings,
                    timer=operation.timer,
                )
            )
            if partial_warnings:
                raise typer.Exit(3)
            return

        ok(f"Client '{name}' removed")
        if partial_warnings:
            for warning in partial_warnings:
                warn(warning.message)
            raise typer.Exit(3)

    err_console.print()
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()


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
    cluster = load_cluster()
    _require_v4_access_user(cluster, name)
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
            panel.enable_user(client.uuid)
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
    cluster = load_cluster()
    temporary_v4_disable = _require_v4_access_user(cluster, name)
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
            panel.disable_user(client.uuid)
        except RemnawaveError as e:
            fail(
                f"Could not disable client '{name}': {e}",
                hint=e.hint or "Check panel connectivity",
                hint_type=e.category,
            )

        if is_json_mode():
            result = build_client_status_result(name, "disabled")
            warnings = (
                [
                    MeridianError(
                        code="MERIDIAN_V4_CLIENT_DISABLE_TEMPORARY",
                        category="user",
                        message=f"Client '{name}' is disabled only until the next V4 apply.",
                        hint="V4 access intent declares managed users active.",
                        exit_code=0,
                    )
                ]
                if temporary_v4_disable
                else []
            )
            emit_json(
                command_envelope(
                    command="client.disable",
                    data=result.to_data(),
                    summary=Summary(
                        text=(
                            f"Temporarily disabled V4 client '{name}'"
                            if temporary_v4_disable
                            else f"Disabled client '{name}'"
                        ),
                        changed=True,
                    ),
                    warnings=warnings,
                    timer=operation.timer,
                )
            )
            return

        ok(f"Client '{name}' disabled")
        if temporary_v4_disable:
            warn("This V4 client is disabled only until the next `meridian apply`")

    err_console.print()
    err_console.print("  [dim]Re-enable:         meridian client enable " + escape(name) + "[/dim]")
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
