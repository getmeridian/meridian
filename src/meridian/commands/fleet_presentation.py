"""Review service-owned settings against the live version, without compiling or applying a fleet."""

from collections.abc import Callable
from typing import Literal

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian import console
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import load_topology_cluster
from meridian.control.client import ControlClient, ControlServiceNotFound
from meridian.core.errors import MeridianError
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.presentation import PresentationPatch, PresentationState
from meridian.i18n import t
from meridian.renderers import emit_json


def presentation_client(cluster: ClusterConfig) -> ControlClient:
    if not cluster.panel.url or not cluster.panel.api_token:
        raise MeridianError(
            "No control-service credentials for this fleet.", category="user", hint="Finish meridian setup first."
        )
    return ControlClient(cluster.panel.url, cluster.panel.api_token)


def live_presentation(client: ControlClient) -> PresentationState:
    try:
        return client.presentation()
    except ControlServiceNotFound as exc:
        raise MeridianError(
            "This control service does not support presentation edits.",
            category="user",
            hint="Upgrade the fleet before changing its settings.",
        ) from exc


def review_presentation(
    client: ControlClient,
    state: PresentationState,
    patch: PresentationPatch,
    *,
    dry_run: bool,
    yes: bool,
    show: Callable[[PresentationState, PresentationState], None],
) -> tuple[PresentationState, bool]:
    try:
        proposed = patch.updated(state)
    except KeyError as exc:
        raise MeridianError(
            "The service does not list this server. Apply the reviewed topology first.", category="user"
        ) from exc
    if proposed == state:
        return state, False
    if not console.is_json_mode():
        show(state, proposed)
    if dry_run:
        return proposed, False
    if not yes:
        if console.is_json_mode() or not console.has_tty():
            console.fail(t("presentation.needs_yes"), hint_type="user")
        if not console.confirm(t("presentation.confirm"), default=False):
            raise typer.Exit(1)
    return client.patch_presentation(patch), True


def run_presentation_value(
    field: Literal["profile_title", "update_interval_hours"], value: str | int | None, *, dry_run: bool, yes: bool
) -> None:
    command = "fleet.title" if field == "profile_title" else "fleet.refresh"
    operation = OperationContext()
    with console.error_context(command, timer=operation.timer):
        cluster = load_topology_cluster()
        if value is None and dry_run:
            console.fail(t("presentation.choose"), hint_type="user")
        try:
            with presentation_client(cluster) as client:
                state = live_presentation(client)
                result, changed = state, False
                if value is not None:
                    patch = PresentationPatch.model_validate({"expected_version": state.version, field: value})
                    result, changed = review_presentation(
                        client,
                        state,
                        patch,
                        dry_run=dry_run,
                        yes=yes,
                        show=lambda old, new: console.info(
                            f"{escape(str(getattr(old, field)))} → {escape(str(getattr(new, field)))}"
                        ),
                    )
        except ValidationError as exc:
            console.fail(str(exc.errors()[0]["msg"]), hint_type="user")
        except MeridianError as exc:
            console.fail(exc)
        status: OutputStatus = "changed" if changed else "no_changes" if value is not None and result == state else "ok"
        if console.is_json_mode():
            emit_json(
                command_envelope(
                    command=command,
                    data={"value": getattr(result, field), "version": result.version, "dry_run": dry_run},
                    summary=Summary(
                        text=("Saved" if changed else "Reviewed" if dry_run else "Read") + " presentation setting",
                        changed=changed,
                    ),
                    timer=operation.timer,
                    status=status,
                )
            )
        elif value is None:
            console.info(escape(str(getattr(result, field))))
        elif changed:
            console.ok(t("presentation.saved"))
