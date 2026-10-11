"""`meridian apply` — converge servers to the reviewed topology in cluster.yml."""

from __future__ import annotations

from meridian.commands._helpers import load_topology_cluster
from meridian.commands.v4_topology import run_v4_apply
from meridian.console import error_context, fail, is_json_mode, set_json_mode
from meridian.core.errors import MeridianError
from meridian.core.output import OperationContext


def run(yes: bool = False, json_output: bool = False) -> None:
    """Review the compiled topology, confirm, then apply it with checkpoints."""
    operation = OperationContext()
    previous_json_mode = is_json_mode()
    if json_output:
        set_json_mode(True)
    try:
        with error_context("apply", timer=operation.timer):
            cluster = load_topology_cluster()
            try:
                run_v4_apply(cluster, yes=yes, json_output=json_output, operation=operation)
            except MeridianError as exc:
                fail(exc)
    finally:
        set_json_mode(previous_json_mode)
