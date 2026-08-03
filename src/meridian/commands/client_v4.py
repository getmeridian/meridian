"""V4 access-user mutation flow for the client command group."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import ReviewedApplyPersistenceError, make_panel, persist_reviewed_apply
from meridian.commands.client_handoff import get_user_or_fail
from meridian.commands.client_state import build_client_persistence_warning
from meridian.console import err_console, fail, is_json_mode, ok, warn
from meridian.core.clients import build_client_add_result
from meridian.core.errors import MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.redaction import redact_string
from meridian.remnawave import MeridianPanel, User
from meridian.renderers import emit_json


def run_add_v4(
    names: list[str],
    cluster: ClusterConfig,
    operation: OperationContext,
    *,
    print_subscription: Callable[[MeridianPanel, User], str],
) -> None:
    """Add access users through the reviewed V4 compiler and resource drivers."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import add_access_users_to_intent
    from meridian.setup.runtime import SetupRuntime

    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("V4 topology intent is missing")
    already_declared = [name for name in names if name in intent.access.users]
    if already_declared:
        fail(
            "Client(s) already declared in V4 topology: " + ", ".join(already_declared),
            hint="Run `meridian apply` to restore a missing declared client.",
            hint_type="user",
        )

    panel = make_panel(cluster)
    with panel:
        for name in names:
            if get_user_or_fail(panel, name, action="check") is not None:
                fail(
                    f"Client '{name}' already exists outside the V4 access intent",
                    hint="Rename the unmanaged panel user or recover its Meridian ownership before adding it.",
                    hint_type="user",
                )

    updated = add_access_users_to_intent(intent, names)
    try:
        result = SetupRuntime(
            ServerRegistry(SERVER_PROFILES_FILE),
            cluster_loader=lambda: cluster,
            persist=persist_reviewed_apply,
        ).apply_intent(updated)
    except MeridianError as exc:
        fail(exc)
    except ReviewedApplyPersistenceError as exc:
        _report_v4_state_failure(names, cluster, operation, exc)
    if not result.all_succeeded:
        failures = "; ".join(f"{item.action.resource.logical_id}: {item.error}" for item in result.failed)
        fail(
            "V4 client access did not converge",
            hint=failures + ". The updated intent was saved; retry `meridian apply` after fixing the failure.",
            hint_type="system",
        )

    succeeded: list[tuple[str, User]] = []
    with make_panel(cluster) as refreshed_panel:
        for name in names:
            user = get_user_or_fail(refreshed_panel, name, action="verify")
            if user is None:
                fail(
                    f"V4 client '{name}' was not observable after apply",
                    hint="Retry `meridian apply` and inspect the access-user resource.",
                    hint_type="system",
                )
            succeeded.append((name, user))

        if is_json_mode():
            result_data = build_client_add_result(succeeded)
            emit_json(
                command_envelope(
                    command="client.add",
                    data=result_data.to_data(),
                    summary=Summary(
                        text=f"Added {len(succeeded)} V4 client(s)",
                        changed=result.changed,
                        counts={"added": len(succeeded), "failed": 0, "page_deploy_failed": 0},
                    ),
                    timer=operation.timer,
                )
            )
            return

        ok(f"Added {len(succeeded)} V4 client(s): {', '.join(names)}")
        handoff_failed = False
        for name, user in succeeded:
            if len(succeeded) > 1:
                err_console.print()
                err_console.print(f"  [bold]— {escape(name)} —[/bold]")
            handoff_error = print_subscription(refreshed_panel, user)
            if isinstance(handoff_error, str) and handoff_error:
                handoff_failed = True
                warn(f"Client '{name}' was added, but its subscription handoff is unavailable: {handoff_error}")

    err_console.print()
    err_console.print("  [dim]View all clients:  meridian client list[/dim]")
    err_console.print()
    if handoff_failed:
        raise typer.Exit(3)


def _report_v4_state_failure(
    names: list[str],
    cluster: ClusterConfig,
    operation: OperationContext,
    exc: Exception,
) -> NoReturn:
    """Report a local-state failure after V4 execution may have mutated the panel."""
    reason = redact_string(str(exc)) or type(exc).__name__
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
            operation="applying V4 client access",
            reason=reason,
        )
        if is_json_mode():
            result = build_client_add_result(observed)
            emit_json(
                command_envelope(
                    command="client.add",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"Observed {len(observed)} requested V4 client(s), but local state was not saved",
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

        ok(f"Requested V4 client(s) now exist: {', '.join(name for name, _user in observed)}")
        warn(warning.message)
        raise typer.Exit(3)

    fail(
        f"V4 client apply could not save local state; remote state may have changed: {reason}",
        hint="Repair cluster.yml persistence, inspect current panel state, then run `meridian plan` before retrying.",
        hint_type="system",
    )
