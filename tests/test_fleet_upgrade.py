"""`fleet upgrade` reads versions read-only, applies only the reviewed plan, then checks the fleet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian import __version__
from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands import fleet_upgrade
from meridian.commands.fleet_upgrade import Component
from meridian.control.client import ControlServiceError
from meridian.control.models import NodeStatus, ServiceHealth
from meridian.control.service import utc_now
from meridian.core.agent_types import NodeStateRef
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.setup.single_server import single_server_intent

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"


def _agent(status: str, running: str = "0.2.0") -> Component:
    return Component("exit-a", "Node agent", running, "0.2.1", status)  # type: ignore[arg-type]


def _run(observations: list[list[Component]], *, yes: bool = True) -> tuple[int, MagicMock, MagicMock]:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    with (
        patch.object(fleet_upgrade, "load_topology_cluster", return_value=cluster),
        patch.object(fleet_upgrade, "_binary_targets", return_value={}),
        patch.object(fleet_upgrade, "_save_pins"),
        patch.object(fleet_upgrade, "_observe", side_effect=observations),
        patch.object(fleet_upgrade, "_apply") as apply,
        patch("meridian.commands.health.run") as check,
        patch.object(fleet_upgrade, "confirm", return_value=False),
    ):
        try:
            fleet_upgrade.run_upgrade(yes=yes)
        except typer.Exit as exc:
            return int(exc.exit_code), apply, check
    return 0, apply, check


def test_a_current_fleet_changes_nothing() -> None:
    code, apply, check = _run([[_agent("current", "0.2.1")]])

    assert code == 0
    apply.assert_not_called()
    check.assert_not_called()


def test_unreadable_servers_stop_before_anything_changes() -> None:
    code, apply, _check = _run([[_agent("upgrade"), Component("relay-a", "Realm", error="ssh: connection refused")]])

    assert code == 3
    apply.assert_not_called()


def test_a_newer_service_stops_before_anything_changes(capsys: pytest.CaptureFixture[str]) -> None:
    newer = Component("control server", "Control service", "9.0.0", __version__, "newer")

    code, apply, _check = _run([[newer, _agent("upgrade")]])

    assert code == 2
    apply.assert_not_called()
    assert "9.0.0" in capsys.readouterr().err


def test_upgrades_apply_the_reviewed_plan_then_check_every_server() -> None:
    code, apply, check = _run([[_agent("upgrade")], [_agent("current", "0.2.1")]])

    assert code == 0
    apply.assert_called_once()
    check.assert_called_once_with()


def test_declined_confirmation_changes_nothing() -> None:
    code, apply, _check = _run([[_agent("upgrade")]], yes=False)

    assert code == 1
    apply.assert_not_called()


def test_a_component_still_behind_after_apply_is_a_system_failure() -> None:
    code, apply, check = _run([[_agent("upgrade")], [_agent("upgrade")]])

    assert code == 3
    apply.assert_called_once()
    check.assert_not_called()


def _status(agent: str | None) -> NodeStatus:
    applied = NodeStateRef(fleet_epoch=1, revision=1, sha256="a" * 64)
    return NodeStatus(
        node_id="main",
        fleet_epoch=1,
        revision=1,
        sha256="a" * 64,
        spec_sha256="b" * 64,
        applied=applied,
        last_seen_at=utc_now(),
        agent_version=agent,
    )


@pytest.mark.parametrize(
    ("service", "node", "expected"),
    [
        (__version__, _status("0.2.1"), ["current", "current"]),
        ("3.9.0", _status("0.2.0"), ["upgrade", "upgrade"]),
        ("9.0.0", None, ["newer", "absent"]),
    ],
    ids=["current", "both-behind", "newer-service-and-unenrolled-node"],
)
def test_service_and_agents_are_judged_against_what_this_meridian_installs(
    service: str, node: NodeStatus | None, expected: list[str]
) -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    client = MagicMock()
    client.__enter__.return_value = client
    client.health.return_value = ServiceHealth(version=service, fleet_id=FLEET_ID, fleet_epoch=1)
    client.node.return_value = node

    with (
        patch("meridian.control.client.ControlClient", return_value=client),
        patch("meridian.control_artifacts.agent_version", return_value="0.2.1"),
    ):
        observed = fleet_upgrade._observe_service(cluster, cluster.topology_intent)  # type: ignore[arg-type]

    assert [component.status for component in observed] == expected
    assert [component.name for component in observed] == ["Control service", "Node agent"]


def test_an_unreachable_service_is_unavailable_evidence() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    client = MagicMock()
    client.__enter__.return_value = client
    client.health.side_effect = ControlServiceError("no answer", retryable=True)

    with patch("meridian.control.client.ControlClient", return_value=client):
        (observed,) = fleet_upgrade._observe_service(cluster, cluster.topology_intent)  # type: ignore[arg-type]

    assert not observed.available


def test_pending_removals_must_be_applied_first() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(
        retiring=(ManagedResourceBinding(logical_id="realm:old:0", resource_kind="realm_hop"),), review_hash="h"
    )

    with patch("meridian.setup.runtime.SetupRuntime", return_value=runtime), pytest.raises(typer.Exit) as exc_info:
        fleet_upgrade._apply(cluster, MagicMock(), cluster.topology_intent)  # type: ignore[arg-type]

    assert exc_info.value.exit_code == 2
    runtime.apply_intent.assert_not_called()


def test_reviewed_apply_binds_the_review_hash() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(retiring=(), review_hash="sha256:review")
    runtime.apply_intent.return_value = ResourceExecutionResult(plan_hash="p", generation=2)

    with patch("meridian.setup.runtime.SetupRuntime", return_value=runtime):
        fleet_upgrade._apply(cluster, MagicMock(), cluster.topology_intent)  # type: ignore[arg-type]

    assert runtime.apply_intent.call_args.kwargs["expected_plan_hash"] == "sha256:review"
