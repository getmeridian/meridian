"""Client traffic limits and expiry: `client set`, `client reset-traffic`, and the usage lines `client show` prints.

Limits are operator-owned panel state on both control backends; applies never touch them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from rich.cells import cell_len
from rich.markup import escape

from meridian.commands._helpers import format_traffic, load_topology_cluster
from meridian.commands.client_handoff import (
    format_status,
    get_user_or_fail,
    resolve_client_mutation_name,
    resolve_client_name,
)
from meridian.console import err_console, error_context, fail, is_json_mode, ok
from meridian.control_access import AccessPanel, access_panel
from meridian.core.client_limits import ClientLimitChange, ClientLimitError, build_limit_change
from meridian.core.clients import ClientDetail, ClientRecord, build_client_limits_result, build_client_record
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError, User
from meridian.renderers import emit_json

_RESET_KEYS = {
    "day": "client.resets.day",
    "week": "client.resets.week",
    "month": "client.resets.month",
    "month_rolling": "client.resets.month_rolling",
}


def parse_limit_options(
    *, limit: str | None, reset: str | None, expires: str | None, devices: str | None = None
) -> ClientLimitChange:
    """The change `--limit`, `--reset`, and `--expires` ask for; a bad value exits 2 before any connection."""
    try:
        return build_limit_change(limit=limit, reset=reset, expires=expires, devices=devices)
    except ClientLimitError as exc:
        fail(exc)


def format_expiry(expires_at: str) -> str:
    """`2026-12-31` for an end-of-day expiry, else the minute in UTC; "never" when empty."""
    if not expires_at:
        return t("client.expires_never")
    moment = datetime.fromisoformat(expires_at)
    if (moment.hour, moment.minute, moment.second) == (23, 59, 59):
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def format_usage(record: ClientRecord) -> str:
    """Used traffic, the limit when there is one, and the reset period when traffic resets."""
    traffic = format_traffic(record.traffic_used_bytes, record.traffic_limit_bytes)
    reset_key = _RESET_KEYS.get(record.traffic_reset)
    return f"{traffic} ({t(reset_key)})" if reset_key else traffic


def print_client_usage(record: ClientRecord) -> None:
    """Status, traffic, expiry, and last-seen lines, aligned in the current language."""
    rows = [
        (t("client.label.status"), format_status(record.status)),
        (t("client.label.traffic"), escape(format_usage(record))),
        (t("client.label.expires"), escape(format_expiry(record.expires_at))),
        (
            t("client.label.devices"),
            str(record.device_limit) if record.device_limit is not None else t("client.device_default"),
        ),
    ]
    if isinstance(record, ClientDetail):
        rows.insert(2, (t("client.label.lifetime"), escape(format_traffic(record.lifetime_traffic_bytes))))
        if record.traffic_reset_at:
            rows.insert(3, (t("client.label.period_started"), escape(record.traffic_reset_at)))
    last_seen = t("client.label.last_seen")
    if record.last_seen:
        rows.append((last_seen, escape(record.last_seen)))
    # One column for every label, shown or not, so values line up the same in every view.
    width = max(cell_len(label) for label in (last_seen, *(label for label, _value in rows))) + 1
    for label, value in rows:
        padding = " " * (width - cell_len(label))
        err_console.print(f"  [bold]{escape(label)}[/bold]{padding}{value}")


def run_set(
    name: str, *, limit: str | None, reset: str | None, expires: str | None, devices: str | None = None
) -> None:
    """Change a client's traffic limit, reset period, or expiry."""
    operation = OperationContext()
    with error_context("client.set", timer=operation.timer):
        change = parse_limit_options(limit=limit, reset=reset, expires=expires, devices=devices)
        if change.is_empty:
            fail(
                "Nothing to change",
                hint="Pass --limit, --reset, --expires, or --devices; for example, --devices 3.",
                hint_type="user",
            )
        _mutate(
            name,
            operation,
            command="client.set",
            verb="set limits for",
            mutate=lambda panel, user: panel.update_user_limits(user.id, change),
            done_key="client.limits_updated",
        )


def run_reset_traffic(name: str) -> None:
    """Start a client's used traffic from zero; a client cut off by their limit can connect again."""
    operation = OperationContext()
    with error_context("client.reset-traffic", timer=operation.timer):
        _mutate(
            name,
            operation,
            command="client.reset-traffic",
            verb="reset traffic for",
            mutate=lambda panel, user: panel.reset_user_traffic(user.id),
            done_key="client.traffic_reset",
        )


def _mutate(
    name: str,
    operation: OperationContext,
    *,
    command: str,
    verb: str,
    mutate: Callable[[AccessPanel, User], User],
    done_key: str,
) -> None:
    cluster = load_topology_cluster()
    # Validate before opening access, preserving the typed name until the service's exact-name check.
    resolve_client_name(cluster, name, console_people=True)
    with access_panel(cluster) as panel:
        name = resolve_client_mutation_name(cluster, panel, name)
        declared = cluster.topology_intent is not None and name in cluster.topology_intent.access.users
        user = get_user_or_fail(panel, name, action="load")
        if user is None:
            if not declared:
                fail(f"Client '{name}' not found", hint="Check names with meridian client list.", hint_type="user")
            fail(
                f"Client '{name}' is declared but missing from the panel",
                hint="Run `meridian apply` to restore it, then retry.",
                hint_type="system",
            )
        try:
            updated = mutate(panel, user)
        except RemnawaveError as exc:
            fail(
                f"Could not {verb} client '{name}': {exc}",
                hint=exc.hint or "Check panel connectivity",
                hint_type=exc.category,
            )

    if is_json_mode():
        emit_json(
            command_envelope(
                command=command,
                data=build_client_limits_result(updated, declared=declared).to_data(),
                summary=Summary(text=f"{verb.capitalize()} client {name}", changed=True, counts={"clients": 1}),
                timer=operation.timer,
            )
        )
        return
    shown = cluster.topology_intent.access.display_name(name) if cluster.topology_intent else name
    ok(t(done_key, name=shown))
    err_console.print()
    print_client_usage(build_client_record(updated))
    err_console.print()
    err_console.print(f"  [dim]{escape(t('client.limits_hint'))}[/dim]")
    err_console.print()
