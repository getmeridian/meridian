"""Compact UTC daily usage for any person the control service holds."""

from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import format_traffic, load_topology_cluster
from meridian.commands.client_handoff import resolve_client_name
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.control.models import UserUsage
from meridian.control_access import access_panel
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_usage(name: str, *, days: str = "30") -> None:
    operation = OperationContext()
    with error_context("client.usage", timer=operation.timer):
        count = parse_days(days)
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                names = [user.username for user in panel.list_users()]
                name = resolve_client_name(cluster, name, console_people=True, console_names=names)
                usage = panel.user_usage(name, days=count)
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="client.usage",
                    data=usage.to_data(),
                    summary=Summary(text=f"Daily usage for {name}", changed=False),
                    timer=operation.timer,
                )
            )
            return
        print_usage(usage)


def parse_days(days: str) -> int:
    """Validate CLI history windows before service access; shared by client and fleet usage."""
    try:
        count = int(days)
        if not 1 <= count <= 730:
            raise ValueError
    except ValueError:
        fail(t("usage.invalid_days"), hint_type="user")
    return count


def print_usage(usage: UserUsage) -> None:
    table = Table(title=t("usage.title", name=usage.username), box=box.SIMPLE, padding=(0, 1))
    for label in ("usage.day", "usage.up", "usage.down", "usage.total"):
        table.add_column(t(label), justify="left" if label == "usage.day" else "right")
    for row in usage.daily:
        table.add_row(
            row.day.isoformat(),
            format_traffic(row.uplink_bytes, 0),
            format_traffic(row.downlink_bytes, 0),
            format_traffic(row.uplink_bytes + row.downlink_bytes, 0),
        )
    err_console.print(table)
    err_console.print(f"  [dim]{escape(t('usage.window', days=usage.days, retention=usage.history_days))}[/dim]")
