"""Daily fleet and server usage through the same service access adapter as client commands."""

from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import format_traffic, load_topology_cluster
from meridian.commands.client_usage import parse_days
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.control_access import access_panel
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_usage(*, days: str = "30") -> None:
    operation = OperationContext()
    with error_context("fleet.usage", timer=operation.timer):
        count = parse_days(days)
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                usage = panel.daily_traffic(days=count)
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="fleet.usage",
                    data=usage.to_data(),
                    summary=Summary(text="Daily fleet usage", changed=False),
                    timer=operation.timer,
                )
            )
            return
        table = Table(title=t("usage.fleet_title"), box=box.SIMPLE, padding=(0, 1))
        for key in ("usage.day", "usage.server", "usage.total"):
            table.add_column(t(key), justify="right" if key == "usage.total" else "left")
        for node in usage.nodes:
            for row in node.daily:
                table.add_row(row.day.isoformat(), escape(node.node_id), format_traffic(row.bytes, 0))
        err_console.print(table)
        err_console.print(f"  [dim]{escape(t('usage.window', days=usage.days, retention=usage.history_days))}[/dim]")
