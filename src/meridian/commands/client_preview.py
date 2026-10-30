"""Explain a synthetic app request without fetching or disclosing a subscription."""

from __future__ import annotations

from pydantic import ValidationError
from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_handoff import resolve_client_mutation_name, resolve_client_name
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.control_access import access_panel
from meridian.core.output import OperationContext, command_envelope
from meridian.core.subscription_preview import PreviewInputs, SubscriptionPreview, SubscriptionPreviewRequest
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_preview(name: str, *, user_agent: str, accept: str = "", suffix: str = "") -> None:
    operation = OperationContext()
    with error_context("client.preview", timer=operation.timer):
        try:
            inputs = PreviewInputs(user_agent=user_agent, accept=accept, suffix=suffix)
        except ValidationError as exc:
            field = str(exc.errors()[0]["loc"][0])
            fail(t(f"preview.invalid_{field}"), hint_type="user")
        cluster = load_topology_cluster()
        resolve_client_name(cluster, name, console_people=True)
        try:
            with access_panel(cluster) as panel:
                name = resolve_client_mutation_name(cluster, panel, name)
                result = panel.preview_subscription(SubscriptionPreviewRequest(username=name, **inputs.to_data()))
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="client.preview",
                    data=result.to_data(),
                    summary="Subscription format preview",
                    timer=operation.timer,
                )
            )
            return
        print_preview(result)


def print_preview(result: SubscriptionPreview) -> None:
    err_console.print("  " + escape(t("preview.title", name=result.username)))
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row(t("preview.format"), t(f"preview.format_{result.format}"))
    table.add_row(t("preview.rule"), t(f"preview.rule_{result.selection.rule}"))
    if result.selection.family:
        table.add_row(t("preview.family"), result.selection.family)
    if result.selection.suffix:
        table.add_row(t("preview.suffix"), result.selection.suffix)
    table.add_row(t("preview.protocols"), ", ".join(result.protocols) or t("preview.none"))
    if result.placeholder:
        table.add_row(t("preview.placeholder"), t(f"preview.placeholder_{result.placeholder}"))
    err_console.print(table)
    entries = Table(box=box.SIMPLE, padding=(0, 1))
    for key in ("preview.entry", "preview.protocol", "preview.result"):
        entries.add_column(t(key))
    for entry in result.included:
        entries.add_row(escape(entry.remark), entry.protocol, t("preview.included"))
    for entry in result.omitted:
        reason = t(f"preview.reason_{entry.reason}")
        if entry.restriction:
            reason += " · " + t(f"preview.restriction_{entry.restriction}")
        entries.add_row(escape(entry.remark), entry.protocol, reason)
    err_console.print(entries)
    err_console.print("  " + escape(t("preview.headers")))
    for name, value in result.response_headers.items():
        err_console.print("  " + escape(name + ": " + value))
