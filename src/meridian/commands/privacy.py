"""Privacy settings belong to the service; this CLI reviews their disclosure before a change."""

from __future__ import annotations

import typer
from pydantic import ValidationError
from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import load_topology_cluster
from meridian.console import confirm, err_console, error_context, fail, info, is_json_mode, ok
from meridian.control.models import PrivacySettings, PrivacyState, PrivacyUpdateResult
from meridian.control_access import access_panel
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json

_DEVICE_LEVELS = ("off", "private", "full")


def run_show() -> None:
    operation = OperationContext()
    with error_context("privacy.show", timer=operation.timer):
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                state = panel.privacy()
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="privacy.show", data=state.to_data(), summary="Privacy settings", timer=operation.timer
                )
            )
            return
        print_state(state)


def run_set(
    *,
    devices: str | None = None,
    device_limit: str | None = None,
    history: str | None = None,
    history_days: str | None = None,
    yes: bool = False,
) -> None:
    operation = OperationContext()
    with error_context("privacy.set", timer=operation.timer):
        change = _parse_change(devices, device_limit, history, history_days)
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                before = panel.privacy()
                settings = PrivacySettings.model_validate({**before.settings.model_dump(), **change})
                if settings == before.settings:
                    result = PrivacyUpdateResult(**before.model_dump(), changed=False)
                else:
                    if is_json_mode() and not yes:
                        fail(
                            t("privacy.confirm_json"),
                            hint="Pass --yes after reviewing meridian privacy show.",
                            hint_type="user",
                        )
                    _print_review(before.settings, settings)
                    if not yes and not confirm(t("privacy.confirm"), default=False):
                        raise typer.Exit(1)
                    result = panel.set_privacy(settings)
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="privacy.set",
                    data=result.to_data(),
                    status="changed" if result.changed else "no_changes",
                    summary=Summary(
                        text="Privacy settings updated" if result.changed else "Privacy settings already match",
                        changed=result.changed,
                    ),
                    timer=operation.timer,
                )
            )
            return
        if result.changed:
            ok(t("privacy.saved"))
        else:
            info(t("privacy.no_changes"))
        _print_settings(result.settings)
        removed = result.deleted
        if removed.devices or removed.traffic_daily or removed.node_traffic_daily:
            info(
                t(
                    "privacy.deleted",
                    devices=removed.devices,
                    users=removed.traffic_daily,
                    nodes=removed.node_traffic_daily,
                )
            )
        if result.rekeyed_devices:
            info(t("privacy.rekeyed", count=result.rekeyed_devices))


def _parse_change(
    devices: str | None, device_limit: str | None, history: str | None, history_days: str | None
) -> dict[str, object]:
    values: dict[str, object] = {}
    if devices is not None:
        values["device_limits"] = devices
    if history is not None:
        values["usage_history"] = history
    for field, value in (("default_device_limit", device_limit), ("history_days", history_days)):
        if value is not None:
            try:
                values[field] = int(value)
            except ValueError:
                fail(t("privacy.integer", field=field), hint_type="user")
    if not values:
        fail(
            t("privacy.nothing"), hint="Pass --devices, --device-limit, --history, or --history-days.", hint_type="user"
        )
    try:
        parsed = PrivacySettings.model_validate(values)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(value) for value in error["loc"])
        fail(t("privacy.invalid", field=field, reason=error["msg"]), hint_type="user")
    return parsed.model_dump(include=parsed.model_fields_set)


def _print_review(before: PrivacySettings, after: PrivacySettings) -> None:
    # Disclosures are printed on stderr even with --yes --json, so stdout stays one envelope.
    lines: list[str] = []
    if _DEVICE_LEVELS.index(after.device_limits) > _DEVICE_LEVELS.index(before.device_limits) or (
        after.device_limits != "off" and after.default_device_limit > before.default_device_limit
    ):
        lines.append(t("privacy.devices_private" if after.device_limits == "private" else "privacy.devices_full"))
        if after.device_limits == "private":
            lines.append(t("privacy.restore_private"))
    if after.usage_history == "daily" and (before.usage_history == "off" or after.history_days > before.history_days):
        lines.append(t("privacy.history_daily", days=after.history_days))
    lines.extend(deletion_disclosures(before, after))
    for text in lines:
        err_console.print("  " + escape(text))


def deletion_disclosures(before: PrivacySettings, after: PrivacySettings) -> list[str]:
    """What changing representation or retention removes; also covers irreversible private → full."""
    lines: list[str] = []
    if before.device_limits != "off" and after.device_limits == "off":
        lines.append(t("privacy.delete_devices"))
    elif (before.device_limits, after.device_limits) == ("full", "private"):
        lines.extend((t("privacy.rekey_devices"), t("privacy.restore_private")))
    elif (before.device_limits, after.device_limits) == ("private", "full"):
        lines.append(t("privacy.reset_private"))
    if before.usage_history == "daily":
        if after.usage_history == "off":
            lines.append(t("privacy.delete_history"))
        elif after.history_days < before.history_days:
            lines.append(t("privacy.prune_history", days=after.history_days))
    return lines


def print_state(state: PrivacyState) -> None:
    _print_settings(state.settings)
    info(t("privacy.devices_off"))
    info(t("privacy.history_off"))
    for label, key in (("private", "privacy.devices_private"), ("full", "privacy.devices_full")):
        err_console.print(f"\n  [bold]{escape(t('privacy.devices'))} · {label}[/bold]")
        info(t(key))
    info(t("privacy.restore_private"))
    err_console.print(f"\n  [bold]{escape(t('privacy.history'))} · daily[/bold]")
    info(t("privacy.history_daily", days=state.settings.history_days))
    err_console.print()
    if not state.changes:
        info(t("privacy.no_changes_yet"))
        return
    table = Table(title=t("privacy.changes"), box=box.SIMPLE, padding=(0, 1))
    for label in ("privacy.when", "privacy.author", "privacy.before", "privacy.after"):
        table.add_column(t(label))
    for change in state.changes:
        table.add_row(
            change.changed_at.replace("T", " ").removesuffix("Z") + " UTC",
            escape(change.author),
            escape(_settings_text(change.before)),
            escape(_settings_text(change.after)),
        )
    err_console.print(table)


def _print_settings(settings: PrivacySettings) -> None:
    table = Table(title=t("privacy.title"), box=box.SIMPLE, padding=(0, 1), show_header=False)
    table.add_column(style="bold")
    table.add_column()
    for key, value in (
        ("privacy.devices", settings.device_limits),
        ("privacy.device_limit", settings.default_device_limit),
        ("privacy.history", settings.usage_history),
        ("privacy.history_days", settings.history_days),
    ):
        table.add_row(t(key), str(value))
    err_console.print(table)


def _settings_text(settings: PrivacySettings) -> str:
    return (
        f"{settings.device_limits} ({settings.default_device_limit}) · "
        f"{settings.usage_history} ({settings.history_days})"
    )
