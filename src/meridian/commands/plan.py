"""Declarative plan command — show what would change without changing anything.

Reads cluster.yml v2 desired state, fetches actual state from the panel
API, and prints a terraform-style diff. Exit codes:
  0 = already converged (no changes needed)
  2 = changes pending
"""

from __future__ import annotations

import typer

from meridian.cluster import ClusterConfig
from meridian.console import err_console, error_context, fail, info
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.plan import build_plan_result
from meridian.reconciler.display import print_plan
from meridian.reconciler.prepare import compute_reconciliation_plan, validate_cluster_for_reconciliation
from meridian.renderers import emit_json


def run(json_output: bool = False) -> None:
    """Show what meridian apply would do, without changing anything."""
    operation = OperationContext()
    with error_context("plan", timer=operation.timer):
        _run(json_output=json_output, operation=operation)


def _run(*, json_output: bool, operation: OperationContext) -> None:
    """Implementation for plan with command metadata already attached."""
    cluster = ClusterConfig.load()
    validate_cluster_for_reconciliation(cluster, "plan")

    info("Fetching actual state from panel...")
    from meridian.remnawave import MeridianPanel, RemnawaveAuthError, RemnawaveError
    from meridian.ssh import ServerConnection

    try:
        # SSH connection to panel host for live subscription page check
        panel_conn = None
        if cluster.panel.server_ip:
            panel_conn = ServerConnection(cluster.panel.server_ip, cluster.panel.ssh_user, port=cluster.panel.ssh_port)

        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            plan = compute_reconciliation_plan(cluster, panel, panel_conn=panel_conn)
    except RemnawaveAuthError as e:
        fail(f"Cannot authenticate to panel: {e}", hint=e.hint, hint_type=e.hint_type)
    except RemnawaveError as e:
        fail(f"Cannot reach panel: {e}", hint_type="system")

    exit_code = 0 if plan.is_empty else 2

    if json_output:
        result = build_plan_result(plan, exit_code=exit_code)
        emit_json(
            command_envelope(
                command="plan",
                data=result.to_data(),
                summary=Summary(
                    text=result.summary,
                    changed=not result.converged,
                    counts=result.counts.model_dump(),
                ),
                status="no_changes" if result.converged else "changed",
                exit_code=exit_code,
                timer=operation.timer,
            )
        )
    else:
        print_plan(plan, console=err_console)

    raise typer.Exit(exit_code)
