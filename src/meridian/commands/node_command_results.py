"""Localized scope disclosure and per-node result rendering shared by client and node commands."""

from __future__ import annotations

from collections.abc import Sequence

from meridian.console import err_console, ok, warn
from meridian.control.command_models import CutOffPlan, NodeCommandOutcome
from meridian.i18n import isolate, t


def cut_off_disclosure(plan: CutOffPlan) -> str:
    nodes = ", ".join(node.node_id for node in plan.nodes)
    if nodes:
        nodes = isolate(nodes)
    if not nodes:
        return t("node_commands.no_inbounds")
    text = t("node_commands.cut_off_scope", nodes=nodes)
    core_nodes = ", ".join(node.node_id for node in plan.nodes if node.effect == "core_restart")
    return text + "\n" + t("node_commands.cut_off_core_scope", nodes=isolate(core_nodes)) if core_nodes else text


def print_command_results(outcomes: Sequence[NodeCommandOutcome]) -> None:
    labels = {
        "done": "node_commands.done",
        "core_restart": "node_commands.core_restart",
        "failed": "node_commands.failed",
        "timed_out": "node_commands.timed_out",
    }
    for outcome in outcomes:
        text = f"{isolate(outcome.node_id)}: {t(labels[outcome.status])}"
        if outcome.status in {"done", "core_restart"}:
            ok(text)
        else:
            warn(text)
        for error in outcome.errors:
            err_console.print("  " + error, markup=False)
