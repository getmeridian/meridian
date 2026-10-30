"""Localized scope disclosure and per-node result rendering shared by client and node commands."""

from __future__ import annotations

from collections.abc import Sequence

from meridian.console import err_console, ok, warn
from meridian.control.command_models import CutOffPlan, NodeCommandOutcome
from meridian.i18n import t


def cut_off_disclosure(plan: CutOffPlan) -> str:
    nodes = ", ".join(node.node_id for node in plan.nodes)
    if nodes:
        nodes = "\u2068" + nodes + "\u2069"
    return t("node_commands.cut_off_scope", nodes=nodes) if nodes else t("node_commands.no_inbounds")


def print_command_results(outcomes: Sequence[NodeCommandOutcome]) -> None:
    labels = {
        "done": "node_commands.done",
        "core_restart": "node_commands.core_restart",
        "failed": "node_commands.failed",
        "timed_out": "node_commands.timed_out",
    }
    for outcome in outcomes:
        text = f"\u2068{outcome.node_id}\u2069: {t(labels[outcome.status])}"
        if outcome.status in {"done", "core_restart"}:
            ok(text)
        else:
            warn(text)
        for error in outcome.errors:
            err_console.print("  " + error, markup=False)
