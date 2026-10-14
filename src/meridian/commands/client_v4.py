"""Access-user mutation flow for the client command group."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import (
    ReviewedApplyPersistenceError,
    apply_reviewed_intent,
    make_panel,
    persist_reviewed_apply,
    require_intent,
)
from meridian.commands.client_handoff import get_user_or_fail, print_handoff_links
from meridian.commands.client_state import build_client_persistence_warning
from meridian.console import err_console, fail, is_json_mode, ok, warn
from meridian.core.clients import build_client_add_result, build_client_remove_result
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.redaction import HANDOFF_LINK_KEYS, redact_string
from meridian.handoff import HandoffLinks
from meridian.remnawave import User
from meridian.renderers import emit_json


def run_add_v4(
    names: list[str],
    cluster: ClusterConfig,
    operation: OperationContext,
    *,
    publish_handoff: Callable[[ClusterConfig, list[str]], list[HandoffLinks]],
) -> None:
    """Add access users through the reviewed compiler and resource drivers."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import add_access_users_to_intent

    intent = require_intent(cluster)
    already_declared = [name for name in names if name in intent.access.users]
    if already_declared:
        fail(
            "Client(s) already declared in the topology: " + ", ".join(already_declared),
            hint="Run `meridian apply` to restore a missing declared client.",
            hint_type="user",
        )

    panel = make_panel(cluster)
    with panel:
        for name in names:
            if get_user_or_fail(panel, name, action="check") is not None:
                fail(
                    f"Client '{name}' already exists in the panel but is not one of your fleet's clients",
                    hint="Rename the unmanaged panel user or recover its Meridian ownership before adding it.",
                    hint_type="user",
                )

    updated = add_access_users_to_intent(intent, names)
    result = apply_reviewed_intent(
        cluster,
        ServerRegistry(SERVER_PROFILES_FILE),
        updated,
        failure="Client access did not converge",
        hint="The updated intent was saved; retry `meridian apply` after fixing the failure.",
        on_unsaved=lambda exc: _report_state_failure(names, cluster, operation, exc),
    )

    succeeded: list[tuple[str, User]] = []
    with make_panel(cluster) as refreshed_panel:
        for name in names:
            user = get_user_or_fail(refreshed_panel, name, action="verify")
            if user is None:
                fail(
                    f"Client '{name}' is missing from the panel after apply",
                    hint="Retry `meridian apply` and inspect the access-user resource.",
                    hint_type="system",
                )
            succeeded.append((name, user))

    handoffs = publish_handoff(cluster, [name for name, _user in succeeded])
    if is_json_mode():
        result_data = build_client_add_result(
            succeeded,
            {links.username: (links.page_url, links.subscription_url) for links in handoffs},
        )
        emit_json(
            command_envelope(
                command="client.add",
                data=result_data.to_data(),
                summary=Summary(
                    text=f"Added {len(succeeded)} client(s)",
                    changed=result.changed,
                    counts={
                        "added": len(succeeded),
                        "failed": 0,
                        "page_deploy_failed": sum(1 for links in handoffs if not links.page_url),
                    },
                ),
                timer=operation.timer,
            ),
            reveal=HANDOFF_LINK_KEYS,
        )
        return

    ok(f"Added {len(succeeded)} client(s): {', '.join(names)}")
    handoff_failed = False
    for links in handoffs:
        if len(succeeded) > 1:
            err_console.print()
            err_console.print(f"  [bold]— {escape(links.username)} —[/bold]")
        if not links.subscription_url:
            handoff_failed = True
            warn(f"Client '{links.username}' was added, but its links are unavailable: {links.error}")
            continue
        if links.error:
            warn(f"Connection page for '{links.username}' is unavailable: {links.error}")
        print_handoff_links(links.subscription_url, page_url=links.page_url)

    err_console.print()
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
    if handoff_failed:
        raise typer.Exit(3)


def _report_state_failure(
    names: list[str],
    cluster: ClusterConfig,
    operation: OperationContext,
    exc: ReviewedApplyPersistenceError,
) -> NoReturn:
    """Report the clients the panel already has when local state could not be saved after apply."""
    reason = redact_string(str(exc.__cause__ or exc))
    observed: list[tuple[str, User]] = []
    try:
        with make_panel(cluster) as panel:
            for name in names:
                user = panel.get_user(name)
                if user is not None:
                    observed.append((name, user))
    except Exception:  # Best-effort recovery must not mask the original post-apply persistence failure.
        observed = []

    if observed:
        warning = build_client_persistence_warning(
            operation="applying client access",
            reason=reason,
        )
        if is_json_mode():
            result = build_client_add_result(observed)
            emit_json(
                command_envelope(
                    command="client.add",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"Observed {len(observed)} requested client(s), but local state was not saved",
                        changed=True,
                        counts={
                            "added": len(observed),
                            "failed": len(names) - len(observed),
                            "state_save_failed": 1,
                        },
                    ),
                    exit_code=3,
                    warnings=[warning],
                    timer=operation.timer,
                )
            )
            raise typer.Exit(3)

        ok(f"Requested client(s) now exist: {', '.join(name for name, _user in observed)}")
        warn(warning.message)
        raise typer.Exit(3)

    fail(exc)


def run_remove_v4(name: str, cluster: ClusterConfig, operation: OperationContext, *, yes: bool) -> None:
    """Remove an access user through the reviewed topology, then delete their page."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.console import confirm
    from meridian.handoff import remove_connection_page
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import remove_access_user_from_intent

    try:
        updated = remove_access_user_from_intent(require_intent(cluster), name)
    except ValueError as exc:
        fail(str(exc), hint="Check client names with: meridian client list", hint_type="user")
    if not yes and not confirm(f"Remove client '{name}'? Their links stop working immediately."):
        raise typer.Exit(1)

    with make_panel(cluster) as panel:
        user = get_user_or_fail(panel, name, action="load")
    apply_reviewed_intent(
        cluster,
        ServerRegistry(SERVER_PROFILES_FILE),
        updated,
        failure=f"Client '{name}' was not fully removed",
        hint="The client is out of the topology; rerun `meridian apply` to finish.",
    )

    page_error = remove_connection_page(cluster, user.vless_uuid) if user and user.vless_uuid else ""
    if page_error:
        warn(f"Client '{name}' was removed, but their connection page could not be deleted: {page_error}")
    else:
        persist_reviewed_apply(cluster)
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.remove",
                data=build_client_remove_result(name).to_data(),
                summary=Summary(text=f"Removed client {name}", changed=True, counts={"removed": 1}),
                exit_code=3 if page_error else 0,
                timer=operation.timer,
            )
        )
    else:
        ok(f"Removed client '{name}'. Their links no longer work.")
    if page_error:
        raise typer.Exit(3)
