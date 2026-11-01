"""Localized scope disclosure and per-node result rendering shared by client and node commands."""

from __future__ import annotations

from collections.abc import Sequence

from meridian.console import err_console, ok, warn
from meridian.control.command_models import CutOffPlan, NodeCommandOutcome
from meridian.core.agent_protocol import ResourceError
from meridian.i18n import isolate, t

COMMAND_ERROR_KEYS = {
    "apply_pending": "node_commands.error_apply_pending",
    "document_not_applied": "node_commands.error_document_not_applied",
    "config_not_installed": "node_commands.error_config_not_installed",
    "config_mismatch": "node_commands.error_config_mismatch",
}


def command_error_text(error: ResourceError) -> str:
    key = COMMAND_ERROR_KEYS.get(error.code)
    return t(key) if key else error.message


def cut_off_disclosure(plan: CutOffPlan) -> str:
    if not plan.nodes:
        return t("node_commands.no_inbounds")
    lines = [t("node_commands.cut_off_scope")]
    for node in plan.nodes:
        shown = isolate(node.node_id)
        if node.effect == "core_restart":
            lines.append(t("node_commands.cut_off_core_node", node=shown))
        else:
            lines.append(
                t("node_commands.cut_off_inbound_node", node=shown, inbounds=isolate(", ".join(node.inbound_tags)))
            )
    return "\n".join(lines)


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
        translated = {
            record.result.error.message: command_error_text(record.result.error)
            for record in outcome.commands
            if record.result is not None and record.result.error is not None
        }
        for error in outcome.errors:
            err_console.print("  " + translated.get(error, error), markup=False)
