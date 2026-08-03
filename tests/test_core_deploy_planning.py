"""Tests for pure deploy planning contracts."""

from __future__ import annotations

import pytest

from meridian.core.deploy_planning import (
    DeployClusterState,
    DeployNodeState,
    DeployPlanningError,
    build_deploy_plan,
    compute_deploy_ports,
)


def test_compute_deploy_ports_is_deterministic() -> None:
    ports = compute_deploy_ports("198.51.100.10")

    assert ports == compute_deploy_ports("198.51.100.10")
    assert 30000 <= ports.xhttp_port < 40000
    assert 10000 <= ports.reality_port < 11000
    assert 20000 <= ports.wss_port < 30000


def test_build_deploy_plan_for_first_deploy_generates_paths() -> None:
    tokens = iter(["panel-secret", "xhttp-path", "ws-path", "info-page"])

    plan = build_deploy_plan(
        "198.51.100.10",
        DeployClusterState(is_configured=False),
        token_hex=lambda _size: next(tokens),
    )

    assert plan.mode == "first_deploy"
    assert plan.secret_path == "panel-secret"
    assert plan.xhttp_path == "xhttp-path"
    assert plan.ws_path == "ws-path"
    assert plan.info_page_path == "info-page"


def test_build_deploy_plan_for_redeploy_reuses_existing_paths() -> None:
    plan = build_deploy_plan(
        "198.51.100.10",
        DeployClusterState(
            is_configured=True,
            panel_secret_path="panel-secret",
            panel_sub_path="info-page",
            existing_node=DeployNodeState(ip="198.51.100.10", xhttp_path="xhttp-path", ws_path="ws-path"),
            node_count=1,
            relay_count=2,
        ),
        token_hex=lambda _size: "unused",
    )

    assert plan.mode == "redeploy"
    assert plan.secret_path == "panel-secret"
    assert plan.xhttp_path == "xhttp-path"
    assert plan.ws_path == "ws-path"
    assert plan.info_page_path == "info-page"
    assert plan.node_count == 1
    assert plan.relay_count == 2


def test_build_deploy_plan_rejects_new_node_on_configured_cluster() -> None:
    with pytest.raises(DeployPlanningError, match="Cluster already configured"):
        build_deploy_plan("198.51.100.20", DeployClusterState(is_configured=True))
