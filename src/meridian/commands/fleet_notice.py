"""Notice reads and confirmed edits use the same service operation as Settings."""

from __future__ import annotations

from typing import Literal

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_handoff import resolve_client_mutation_name
from meridian.console import confirm, err_console, error_context, fail, is_json_mode, ok
from meridian.control_access import access_panel
from meridian.core.models import Summary
from meridian.core.notices import NOTICE_FIELDS, Notice, NoticeState, NoticeTextError, NoticeWrite, parse_notice_until
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_notice(
    action: Literal["show", "set", "clear"],
    *,
    text: str | None = None,
    until: str | None = None,
    for_user: str | None = None,
    yes: bool = False,
) -> None:
    operation = OperationContext()
    command = "fleet.notice." + action
    with error_context(command, timer=operation.timer):
        candidate = None
        if action == "set":
            try:
                candidate = Notice(text=text or "", until=parse_notice_until(until) if until is not None else None)
            except ValidationError as exc:
                cause = exc.errors()[0].get("ctx", {}).get("error")
                if isinstance(cause, NoticeTextError):
                    fields = ", ".join("{" + field + "}" for field in NOTICE_FIELDS)
                    fail(t(f"notice.error_{cause.code}", fields=fields), hint_type="user")
                fail(t("notice.invalid", reason=exc.errors()[0]["msg"].removeprefix("Value error, ")), hint_type="user")
            except ValueError:
                fail(t("notice.until_invalid"), hint_type="user")
        if action != "show" and is_json_mode() and not yes:
            fail(t("notice.confirm_json"), hint="Pass --yes after reviewing the notice.", hint_type="user")
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                if for_user is not None:
                    for_user = resolve_client_mutation_name(cluster, panel, for_user)
                if action == "show":
                    state = panel.notice(for_user)
                else:
                    request = NoticeWrite(notice=candidate, for_user=for_user)
                    preview = panel.set_notice(request)
                    print_notice(preview)
                    if not yes and not confirm(
                        t("notice.clear_confirm" if action == "clear" else "notice.confirm"), default=False
                    ):
                        raise typer.Exit(1)
                    state = panel.set_notice(request.model_copy(update={"confirm": True}))
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command=command,
                    data=state.to_data(),
                    status="ok" if action == "show" else "changed" if state.changed else "no_changes",
                    summary=Summary(text="Notice read" if action == "show" else "Notice saved", changed=state.changed),
                    timer=operation.timer,
                ),
                reveal=frozenset({"data.notice.text", "data.preview"}),
            )
            return
        if action == "show":
            print_notice(state)
        else:
            ok(t("notice.cleared" if action == "clear" else "notice.saved"))


def print_notice(state: NoticeState) -> None:
    err_console.print(
        "  " + escape(t("notice.preview_for", name=state.for_user))
        if state.for_user
        else "  " + escape(t("notice.sample"))
    )
    err_console.print("  " + escape(state.preview or t("notice.none")))
    if state.notice is not None:
        err_console.print("  " + escape(t("notice.until", when=state.notice.until or t("notice.no_expiry"))))
        if not state.active:
            err_console.print("  " + escape(t("notice.expired")))
    fields = ", ".join("{" + field + "}" for field in NOTICE_FIELDS)
    err_console.print("  " + escape(t("notice.allowed", fields=fields)))
