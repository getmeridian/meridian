"""Immediate account creation and deletion through the control service."""

from __future__ import annotations

import typer
from pydantic import ValidationError

from meridian.cluster import ClusterConfig
from meridian.commands.client_handoff import print_handoff_links
from meridian.commands.node_command_results import cut_off_disclosure, print_command_results
from meridian.console import confirm, fail, is_json_mode, ok, warn
from meridian.control_access import access_panel
from meridian.core.client_limits import ClientLimitChange
from meridian.core.clients import ClientAddFailure, ClientAddResult, build_client_add_result, build_client_remove_result
from meridian.core.errors import MeridianError
from meridian.core.models import ErrorModel, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_creation import creation_request
from meridian.core.redaction import HANDOFF_LINK_KEYS, redact_string
from meridian.handoff import user_handoff
from meridian.i18n import t
from meridian.node_commands import command_exit_code, command_warnings, cut_off_scope, wait_commands
from meridian.remnawave import RemnawaveAuthError, User
from meridian.renderers import emit_json


def run_add_v4(
    names: list[str], cluster: ClusterConfig, operation: OperationContext, *, limits: ClientLimitChange
) -> None:
    """Validate and check every name before the first POST; report each request's outcome."""
    try:
        settings = limits.for_creation()
        requests = [creation_request(name, settings) for name in names]
    except (ValidationError, ValueError):
        fail("Invalid client name or creation settings.", hint_type="user")
    created: dict[int, tuple[str, User]] = {}
    failed: dict[int, ClientAddFailure] = {}
    with access_panel(cluster) as panel:
        panel.require_people_service()
        for name in names:
            existing = panel.matching_users(name)
            if existing:
                person = existing[0]
                fail(
                    f"Client '{name}' already exists: {person.display_name or person.username} ({person.username}).",
                    hint_type="user",
                )
        # Reserve requested usernames before auto-assigned ones can claim a later batch name.
        ordered = sorted(enumerate(requests), key=lambda item: (item[1].username is None, item[0]))
        for index, request in ordered:
            name = names[index]
            try:
                user = panel.create_user(request)
            except RemnawaveAuthError:
                raise
            except MeridianError as exc:
                failed[index] = ClientAddFailure(name=name, error=redact_string(str(exc)))
                continue
            created[index] = (user.username, user)
    succeeded = [created[index] for index in sorted(created)]
    failures = [failed[index] for index in sorted(failed)]
    links = [user_handoff(cluster, user) for _name, user in succeeded]
    result = build_client_add_result(
        succeeded, {link.username: (link.page_url, link.subscription_url) for link in links}
    )
    result = ClientAddResult(clients=result.clients, failures=failures)
    code = 3 if failures else 0
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.add",
                data=result.to_data(),
                summary=Summary(
                    text=f"Added {len(succeeded)} client(s)",
                    changed=bool(succeeded),
                    counts={"added": len(succeeded), "failed": len(failures)},
                ),
                exit_code=code,
                status="failed" if failures else "ok",
                errors=[
                    ErrorModel(
                        code="MERIDIAN_CLIENT_ADD_FAILED",
                        category="system",
                        message=f"Could not add '{failure.name}': {failure.error}",
                        retryable=False,
                        exit_code=code,
                    )
                    for failure in failures
                ],
                timer=operation.timer,
            ),
            reveal=HANDOFF_LINK_KEYS,
        )
    else:
        for link in links:
            ok(f"Added client '{link.name}'")
            print_handoff_links(link.subscription_url, page_url=link.page_url)
        for failure in failures:
            warn(f"Could not add '{failure.name}': {failure.error}")
    if code:
        raise typer.Exit(code)


def run_remove_v4(
    name: str, cluster: ClusterConfig, operation: OperationContext, *, yes: bool, cut_off: bool = False
) -> None:
    """Delete one person, with the same atomic optional cut-off as the owner panel."""
    with access_panel(cluster) as panel:
        panel.require_people_service()
        user = panel.get_user(name)
        if user is None:
            fail(f"Client '{name}' not found", hint_type="user")
        shown = user.display_name or name
        scope = cut_off_scope(cluster, name) if cut_off else None
        text = t("node_commands.remove_confirm", name=shown)
        if scope is not None:
            text += "\n\n" + cut_off_disclosure(scope)
            if yes and not is_json_mode():
                warn(cut_off_disclosure(scope))
        if not yes and not confirm(text):
            raise typer.Exit(1)
        commands = []
        if cut_off:
            commands = panel.delete_user_with_cut_off(name).commands
        else:
            panel.delete_user(name)
    outcomes = wait_commands(cluster, commands) if cut_off else None
    code = command_exit_code(outcomes or [])
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.remove",
                data=build_client_remove_result(name, display_name=user.display_name, cut_off=outcomes).to_data(),
                summary=Summary(text=f"Removed client {shown}", changed=True, counts={"removed": 1}),
                exit_code=code,
                warnings=command_warnings(outcomes or []),
                timer=operation.timer,
            )
        )
    else:
        ok(f"Removed client '{shown}'. Their links no longer work.")
        print_command_results(outcomes or [])
    if code:
        raise typer.Exit(code)
