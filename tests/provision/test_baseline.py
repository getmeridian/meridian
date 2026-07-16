"""Tests for the shared server-baseline recipe."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from meridian.provision import (
    build_node_steps,
    build_server_baseline_checks,
    build_server_baseline_steps,
    build_setup_steps,
)
from meridian.provision.recipe import Operation, Resource
from meridian.provision.steps import ProvisionContext


@pytest.mark.parametrize(
    ("harden", "expected_names"),
    [
        (
            False,
            [
                "Check disk space",
                "Install system packages",
                "Enable automatic security updates",
                "Set timezone to UTC",
                "Enable BBR congestion control",
                "Ensure port 443",
            ],
        ),
        (
            True,
            [
                "Check disk space",
                "Install system packages",
                "Enable automatic security updates",
                "Set timezone to UTC",
                "Harden SSH configuration",
                "Configure fail2ban",
                "Enable BBR congestion control",
                "Configure firewall",
            ],
        ),
    ],
)
def test_server_baseline_preserves_order(harden: bool, expected_names: list[str]) -> None:
    ctx = ProvisionContext(ip="198.51.100.1", harden=harden)

    steps = build_server_baseline_steps(ctx, install_docker=False)

    assert [step.name for step in steps] == expected_names
    assert all(isinstance(step, Operation) for step in steps)


def test_server_baseline_appends_docker_only_when_requested() -> None:
    ctx = ProvisionContext(ip="198.51.100.1")

    without_docker = build_server_baseline_steps(ctx, install_docker=False)
    with_docker = build_server_baseline_steps(ctx, install_docker=True)

    assert [step.name for step in with_docker[:-1]] == [step.name for step in without_docker]
    docker = with_docker[-1]
    assert docker.name == "Install Docker"
    assert docker.requires == frozenset({Resource.SYSTEM_PACKAGES})
    assert docker.provides == frozenset({Resource.DOCKER_INSTALLED})


def test_declarative_baseline_leaves_public_ports_to_compiled_firewall_resources() -> None:
    ctx = ProvisionContext(ip="198.51.100.1", harden=True)

    steps = build_server_baseline_steps(
        ctx,
        install_docker=False,
        manage_public_ports=False,
    )
    firewall = next(step for step in steps if step.name == "Configure firewall")
    checks = build_server_baseline_checks(
        ctx,
        install_docker=False,
        manage_public_ports=False,
    )

    assert firewall.provides == frozenset({Resource.FIREWALL_CONFIGURED})
    assert firewall.manage_public_ports is False
    assert "https_firewall" not in {check.name for check in checks}
    assert "hysteria_firewall" not in {check.name for check in checks}


@pytest.mark.parametrize("harden", [False, True])
@pytest.mark.parametrize("builder", [build_setup_steps, build_node_steps])
def test_deploy_recipes_keep_baseline_as_their_prefix(
    harden: bool,
    builder: Callable[[ProvisionContext], list[Operation]],
) -> None:
    ctx = ProvisionContext(ip="198.51.100.1", harden=harden)
    baseline = build_server_baseline_steps(ctx, install_docker=True)

    steps = builder(ctx)

    assert [step.name for step in steps[: len(baseline)]] == [step.name for step in baseline]
