"""Rotate declared people's connection keys while preserving their subscription URLs."""

from __future__ import annotations

import typer

from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.commands.client_handoff import validate_client_name
from meridian.commands.node_command_results import cut_off_disclosure, print_command_results
from meridian.console import confirm, error_context, fail, is_json_mode, ok, warn
from meridian.control.client import ControlServiceCredentialsChanged, ControlServiceError
from meridian.control_access import access_panel
from meridian.core.client_credentials import ClientCredentialRotationResult, RotatedClient
from meridian.core.client_sessions import CredentialRotationRequest
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import isolate, t
from meridian.node_commands import command_exit_code, command_warnings, cut_off_scope, wait_commands
from meridian.renderers import emit_json


def run_rotate_credentials(name: str, *, yes: bool = False, cut_off: bool = False) -> None:
    operation = OperationContext()
    with error_context("client.rotate-credentials", timer=operation.timer):
        if is_json_mode() and not yes:
            fail(t("client.credentials_requires_yes"), hint=t("client.credentials_yes_hint"), hint_type="user")
        cluster = load_topology_cluster()
        access = require_intent(cluster).access
        found = access.find_user(name)
        if found is None:
            fail(
                t("client.credentials_not_declared", name=isolate(name)),
                hint=t("client.credentials_console_hint"),
                hint_type="user",
            )
        name = found
        validate_client_name(name)
        shown = isolate(access.display_name(name))
        try:
            with access_panel(cluster) as panel:
                before = panel.user_credentials(name)
                if before is None:
                    fail(
                        t("client.credentials_missing", name=shown),
                        hint=t("client.credentials_apply_hint"),
                        hint_type="system",
                    )
                request = CredentialRotationRequest(expected_generation=before.credential_generation, cut_off=cut_off)
                scope = cut_off_scope(cluster, name) if cut_off else None
                text = t("client.credentials_confirm", name=shown)
                if scope is not None:
                    text += "\n\n" + cut_off_disclosure(scope)
                    if yes and not is_json_mode():
                        warn(cut_off_disclosure(scope))
                if not yes and not confirm(text):
                    raise typer.Exit(1)
                rotated = panel.rotate_credentials(name, request)
        except ControlServiceCredentialsChanged as exc:
            if is_json_mode():
                fail(exc)
            fail(t("client.credentials_changed"), hint=t("client.credentials_refresh_hint"), hint_type="user")
        except ControlServiceError as exc:
            fail(exc)
        outcomes = wait_commands(cluster, rotated.commands) if cut_off else None
        code = command_exit_code(outcomes or [])
        data = ClientCredentialRotationResult(
            client=RotatedClient(
                username=name,
                id=rotated.user.user_id,
                status=rotated.user.status,
                credential_generation=rotated.user.credential_generation,
            ),
            flow_blocks=rotated.flow_blocks,
            cut_off=outcomes,
        )
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="client.rotate-credentials",
                    data=data.to_data(),
                    summary=Summary(
                        text=f"Rotated connection credentials for {name}", changed=True, counts={"rotated": 1}
                    ),
                    exit_code=code,
                    warnings=command_warnings(outcomes or []),
                    timer=operation.timer,
                )
            )
        else:
            ok(t("client.credentials_rotated", name=shown))
            ok(t("client.credentials_refresh"))
            print_command_results(outcomes or [])
            labels = {
                "blocked": "node_commands.flow_blocked",
                "pending_node": "node_commands.flow_pending",
                "agent_too_old": "node_commands.flow_old_agent",
            }
            for node in rotated.flow_blocks.nodes:
                message = isolate(node.node_id) + ": " + t(labels[node.status])
                (ok if node.status == "blocked" else warn)(message)
        if code:
            raise typer.Exit(code)
