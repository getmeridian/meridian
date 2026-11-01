"""Human schedule dates and reviewed, read-only previews of person edits."""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.console import confirm, err_console, fail, is_json_mode
from meridian.control_access import AccessPanel
from meridian.core.account_schedule import LIFECYCLE_FIELDS, AccountSchedulePreview
from meridian.core.client_limits import ClientLimitChange
from meridian.core.clients import ClientLimitsResult, ClientRecord, build_client_record
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import User
from meridian.renderers import emit_json


def account_expiry(record: ClientRecord) -> str:
    from meridian.commands.client_limits import format_expiry

    if not record.expires_at and record.activation_duration_days is not None:
        return first_use_text(record.activation_duration_days)
    return format_expiry(record.expires_at)


def first_use_text(days: int) -> str:
    return t("lifecycle.valid_from_first_use_one" if days == 1 else "lifecycle.valid_from_first_use", days=days)


def print_schedule(preview: AccountSchedulePreview) -> None:
    from meridian.commands.client_limits import format_expiry

    expiry = (
        first_use_text(preview.activation_duration_days or 1)
        if preview.activation_pending
        else format_expiry(preview.expire_at or "")
    )
    rows = [("lifecycle.expiry", expiry)]
    if preview.next_reset_at:
        rows.append(("lifecycle.next_reset", format_expiry(preview.next_reset_at)))
    if preview.renewal_enabled:
        next_at = (
            format_expiry(preview.next_renewal_at)
            if preview.next_renewal_at
            else t("lifecycle.waiting_first_use")
            if preview.activation_pending
            else t("lifecycle.no_renewals_left")
        )
        rows.append(("lifecycle.next_renewal", next_at))
        rows.append(
            (
                "lifecycle.renewals_left",
                str(preview.renewals_left) if preview.renewals_left is not None else t("lifecycle.unlimited_renewals"),
            )
        )
    for key, value in rows:
        err_console.print("  " + escape(t(key)) + ": " + escape(value))


def review_limit_schedule(
    panel: AccessPanel,
    user: User,
    change: ClientLimitChange,
    operation: OperationContext,
    *,
    declared: bool,
    dry_run: bool,
    yes: bool,
) -> bool:
    """Return whether to apply; a dry run emits the same service-calculated dates."""
    scheduled = bool(change.model_fields_set & set(LIFECYCLE_FIELDS)) or change.traffic_reset == "month_rolling"
    scheduled = scheduled or (
        change.sets_expiry and (user.renewal_interval_days is not None or user.renewal_interval_months is not None)
    )
    if not scheduled and not dry_run:
        return True
    preview = panel.preview_user_limits(user.id, change)
    if dry_run:
        if is_json_mode():
            result = ClientLimitsResult(
                client=build_client_record(user, declared=declared), schedule_preview=preview, applied=False
            )
            emit_json(
                command_envelope(
                    command="client.set",
                    data=result.to_data(),
                    timer=operation.timer,
                    summary=Summary(text=f"Preview schedule for {user.username}"),
                )
            )
        else:
            print_schedule(preview)
        return False
    if not is_json_mode():
        print_schedule(preview)
    if not yes:
        if is_json_mode():
            fail(t("lifecycle.needs_yes"), hint=t("lifecycle.review_hint"), hint_type="user")
        if not confirm(t("lifecycle.confirm", name=user.username), default=False):
            raise typer.Exit(1)
    return True
