"""`meridian plan` — show what `meridian apply` would change, without changing it.

Exit codes:
  0 = already converged
  1 = nothing to plan (no fleet, or unusable local state)
  2 = changes pending
  3 = required observation evidence was unavailable
"""

from __future__ import annotations

from meridian.commands._helpers import load_topology_cluster
from meridian.commands.v4_topology import run_v4_plan
from meridian.console import error_context, fail, is_json_mode
from meridian.core.errors import MeridianError
from meridian.core.output import OperationContext


def run() -> None:
    """Inspect the compiled topology against live servers and the panel."""
    operation = OperationContext()
    # Exit 2 means "changes pending" to CI, so user errors exit 1 here.
    with error_context("plan", timer=operation.timer, user_error_exit_code=1):
        cluster = load_topology_cluster()
        try:
            run_v4_plan(cluster, json_output=is_json_mode(), operation=operation)
        except MeridianError as exc:
            fail(exc)
