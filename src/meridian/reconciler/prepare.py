"""Shared plan preparation logic for ``meridian plan`` and ``meridian apply``.

Both commands need the same pre-computation steps:
1. Validate the cluster has desired state and a configured panel.
2. Build desired + actual state from cluster config and panel API.
3. Load applied-state snapshots and compute the reconciliation plan.

This module extracts that shared sequence so plan.py and apply.py
each call it once and then diverge (plan displays, apply executes).
"""

from __future__ import annotations

from meridian.cluster import ClusterConfig
from meridian.console import fail
from meridian.reconciler.diff import Plan, compute_plan
from meridian.reconciler.state import build_actual_state, build_desired_state


def validate_cluster_for_reconciliation(cluster: ClusterConfig, command: str) -> None:
    """Guard: cluster must have desired state and a configured panel.

    Calls ``fail()`` (which raises ``typer.Exit``) on validation errors.
    """
    has_desired = (
        cluster.desired_nodes is not None or cluster.desired_clients is not None or cluster.desired_relays is not None
    )
    has_sub_page = cluster.subscription_page and (
        cluster.subscription_page.enabled or cluster.subscription_page.deployed
    )
    if not has_desired and not has_sub_page:
        fail(
            "No desired state defined in cluster.yml",
            hint=(
                f"Add desired_nodes, desired_clients, or desired_relays to cluster.yml,\nthen run: meridian {command}"
            ),
            hint_type="user",
        )

    if not cluster.is_configured:
        fail(
            "No panel configured — deploy first with: meridian deploy <IP>",
            hint_type="user",
        )


def compute_reconciliation_plan(
    cluster: ClusterConfig,
    panel: object,
    panel_conn: object | None = None,
) -> Plan:
    """Build desired + actual state and compute the reconciliation plan.

    Args:
        cluster: Loaded cluster configuration with desired state.
        panel: An active ``MeridianPanel`` instance (kept generic for
            testability — callers manage the context-manager lifecycle).
        panel_conn: Optional SSH connection to the panel host for live
            subscription page status checks.

    Returns:
        A ``Plan`` with typed actions ready for display or execution.
    """
    from meridian.operations import load_applied_snapshot

    desired = build_desired_state(cluster)
    actual = build_actual_state(cluster, panel, panel_conn=panel_conn)

    return compute_plan(
        desired,
        actual,
        applied_clients=load_applied_snapshot(cluster, "desired_clients_applied"),
        applied_node_hosts=load_applied_snapshot(cluster, "desired_nodes_applied"),
        applied_relay_hosts=load_applied_snapshot(cluster, "desired_relays_applied"),
    )
