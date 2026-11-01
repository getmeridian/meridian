"""Read the fleet's local lifecycle ledger, without exposing credentials or report errors."""

from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import load_topology_cluster
from meridian.console import err_console, error_context, fail, info, is_json_mode
from meridian.control.client import ControlClient, ControlServiceError
from meridian.core.agent_types import MAX_SAFE_INTEGER
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.fleet_events import (
    MAX_EVENTS,
    BulkEventDetail,
    FleetEvent,
    NodeEventDetail,
    PrivacyEventDetail,
    TokenEventDetail,
    UserFieldsDetail,
)
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.queries import bounded_integer
from meridian.i18n import t
from meridian.renderers import emit_json


def run_events(*, since: str = "0", limit: str = "100") -> None:
    operation = OperationContext()
    with error_context("fleet.events", timer=operation.timer):
        try:
            after = bounded_integer(since, "since", 0, MAX_SAFE_INTEGER)
            count = bounded_integer(limit, "limit", 1, MAX_EVENTS)
        except ValueError as exc:
            fail(MeridianError(str(exc), category="user"))
        cluster = load_topology_cluster()
        if not cluster.panel.url or not cluster.panel.api_token:
            fail(LocalStateError(t("events.credentials_missing"), hint=t("events.deploy_first")))
        try:
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                page = client.events(after=after, limit=count)
        except ControlServiceError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="fleet.events",
                    data=page.to_data(),
                    summary=Summary(
                        text=f"{len(page.events)} events", changed=False, counts={"events": len(page.events)}
                    ),
                    timer=operation.timer,
                )
            )
            return
        if not page.events:
            info(t("events.empty"))
            return
        table = Table(title=t("events.title"), box=box.ROUNDED, padding=(0, 1))
        table.add_column("#", justify="right")
        for key in (
            "events.label.time",
            "events.label.kind",
            "events.label.subject",
            "events.label.actor",
            "events.label.detail",
        ):
            table.add_column(t(key))
        for event in page.events:
            actor = t(f"events.actor.{event.actor}") if event.actor in ("root", "owner", "system") else event.actor
            table.add_row(
                str(event.id),
                event.time.replace("T", " ").removesuffix("Z") + " UTC",
                escape(t(f"events.kind.{event.kind}")),
                escape(event.subject),
                escape(actor),
                escape(_detail(event)),
            )
        err_console.print()
        err_console.print(table)
        err_console.print(t("events.cursor", id=page.next_cursor))
        if page.has_more:
            err_console.print(t("events.more", id=page.next_cursor))
        err_console.print()


def _detail(event: FleetEvent) -> str:
    detail = event.detail
    if isinstance(detail, BulkEventDetail):
        return t(
            "events.bulk",
            action=t(f"bulk.action.{detail.action}"),
            changed=detail.changed,
            targeted=detail.targeted,
            cli_required=detail.cli_required,
        )
    if isinstance(detail, UserFieldsDetail):
        return t("events.changed", fields=", ".join(t(f"events.field.{field}") for field in detail.fields))
    if isinstance(detail, TokenEventDetail):
        return t("events.scope", scope=detail.scope)
    if isinstance(detail, PrivacyEventDetail):
        return t("events.audit", id=detail.audit_id)
    if isinstance(detail, NodeEventDetail) and detail.failed_resources:
        return t("events.failed_resources", count=detail.failed_resources)
    return ""
