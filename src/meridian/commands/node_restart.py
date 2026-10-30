"""Restart held, managed node units through the signed-command API; no SSH path."""

from __future__ import annotations

import typer

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.commands._helpers import load_topology_cluster
from meridian.commands._validation import validate_command_input
from meridian.commands.node_command_results import print_command_results
from meridian.console import confirm, error_context, fail, is_json_mode, warn
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.command_models import NodeCommandRecord, RestartUnitCreate
from meridian.core.models import Summary
from meridian.core.node_commands import NodeRestartRequest, NodeRestartResult
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.node_commands import command_exit_code, command_warnings, wait_for_commands, with_command_issue_error
from meridian.renderers import emit_json


def run_restart(name: str, *, core: bool = False, all_units: bool = False, yes: bool = False) -> None:
    operation = OperationContext()
    with error_context("node.restart", timer=operation.timer):
        if core and all_units:
            fail(t("node_commands.restart_exclusive"), hint_type="user")
        request = validate_command_input(
            NodeRestartRequest,
            "Invalid node restart request",
            ip_or_name=name,
            scope="all" if all_units else "core",
            yes=yes,
        )
        if is_json_mode() and not yes:
            fail(t("node_commands.restart_confirmation"), hint="Pass --yes to confirm the restart.", hint_type="user")
        cluster = load_topology_cluster()
        topology = topology_from_local_cluster(cluster)
        matches = [node for node in topology.nodes if request.ip_or_name in {node.name, node.uuid, node.ip}]
        if len(matches) != 1 or not matches[0].uuid:
            fail(
                t("node_commands.restart_unknown", name=name),
                hint="Read the node names with: meridian node list",
                hint_type="user",
            )
        node_id = matches[0].uuid
        commands: list[NodeCommandRecord] = []
        issue_error: ControlServiceError | None = None
        try:
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                targets = client.command_targets(node_id)
                units = targets.units if request.scope == "all" else [unit for unit in targets.units if unit.core]
                if not units:
                    fail(
                        t("node_commands.restart_no_units"),
                        hint="Use --all for other managed units, or run meridian apply.",
                        hint_type="user",
                    )
                disclosure = (
                    t("node_commands.restart_all_confirm", node=node_id, units=", ".join(unit.name for unit in units))
                    if all_units
                    else t("node_commands.restart_core_confirm", node=node_id)
                )
                if not yes and not confirm(disclosure):
                    raise typer.Exit(1)
                if yes and not is_json_mode():
                    warn(disclosure)
                for unit in units:
                    try:
                        commands.append(client.issue_command(node_id, RestartUnitCreate(resource_id=unit.resource_id)))
                    except ControlServiceError as exc:
                        # An uncertain POST may already have queued a restart; do not replace it with another ID.
                        issue_error = exc
                        break
                outcomes = wait_for_commands(client, commands)
        except ControlServiceError as exc:
            fail(exc)
        if issue_error is not None:
            previous = outcomes[0] if outcomes else None
            outcomes = [with_command_issue_error(node_id, previous, issue_error, secrets=cluster.secret_values())]
        result = NodeRestartResult(scope=request.scope, nodes=outcomes)
        exit_code = command_exit_code(outcomes)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="node.restart",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"Node restart results for {node_id}",
                        changed=bool(commands),
                        counts={
                            "nodes": len(outcomes),
                            "failed": sum(outcome.status == "failed" for outcome in outcomes),
                            "timed_out": sum(outcome.status == "timed_out" for outcome in outcomes),
                        },
                    ),
                    exit_code=exit_code,
                    warnings=command_warnings(outcomes),
                    timer=operation.timer,
                )
            )
        else:
            print_command_results(outcomes)
        if exit_code:
            raise typer.Exit(exit_code)
