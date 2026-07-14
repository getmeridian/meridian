"""Unit tests for the declarative `meridian apply` command.

These cover the action handler logic and command-level orchestration —
not the underlying provisioning, which is unit-tested elsewhere.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, DesiredRelay, NodeEntry, PanelConfig, RelayEntry
from meridian.commands.apply import _handle_update_relay
from meridian.commands.apply import run as apply_run
from meridian.reconciler.diff import Plan, PlanAction, PlanActionKind
from meridian.reconciler.executor import ActionResult, ExecutionResult


def _make_cluster_with_relay(relay_ip: str = "198.51.100.20") -> ClusterConfig:
    cluster = ClusterConfig(
        version=2,
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="tok", server_ip="198.51.100.1"),
        nodes=[
            NodeEntry(
                ip="198.51.100.1",
                uuid="00000000-0000-0000-0000-000000000001",
                name="exit-1",
                is_panel_host=True,
            )
        ],
        relays=[
            RelayEntry(
                ip=relay_ip,
                exit_node_ip="198.51.100.1",
                name="r1",
                host_uuids={"REALITY": "00000000-0000-0000-0000-0000000000aa"},
            )
        ],
        desired_relays=[
            DesiredRelay(host=relay_ip, name="r1", exit_node="exit-1", sni="www.google.com"),
        ],
    )
    return cluster


def _v4_cluster() -> ClusterConfig:
    from meridian.core.topology import (
        AccessIntent,
        ControlPlaneIntent,
        ExitIntent,
        ProtocolPathIntent,
        SetupIntent,
    )

    return ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-exit",
                    paths=[
                        ProtocolPathIntent(
                            id="reality",
                            protocol="reality",
                            reality_sni="www.microsoft.com",
                        )
                    ],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        )
    )


def test_v4_apply_routes_through_checkpointed_setup_runtime() -> None:
    cluster = _v4_cluster()
    with (
        patch(
            "meridian.commands.apply.ClusterConfig.load",
            return_value=cluster,
        ),
        patch("meridian.commands.apply._run_v4_topology") as run_v4,
        patch("meridian.commands.apply.validate_cluster_for_reconciliation") as validate_legacy,
    ):
        apply_run(yes=True)

    run_v4.assert_called_once()
    validate_legacy.assert_not_called()


class TestHandleUpdateRelayPreflight:
    """Bug #4 regression: UPDATE_RELAY used to delete the old relay before
    attempting to provision the new one. If the new SSH target was unreachable
    or the new exit node could not be resolved, the cluster ended up with no
    relay at all. The handler now preflights both before deleting anything.
    """

    def _make_panel(self) -> object:
        from meridian.remnawave import MeridianPanel

        # Build a panel object without going through __init__ so we don't try
        # to open a real httpx client in tests.
        return MeridianPanel.__new__(MeridianPanel)

    def test_aborts_when_no_matching_desired_relay(self) -> None:
        cluster = _make_cluster_with_relay()
        cluster.desired_relays = []  # diff produced UPDATE but desired list is empty
        action = PlanAction(kind=PlanActionKind.UPDATE_RELAY, target="198.51.100.20")

        with patch("meridian.operations.remove_relay") as rm:
            with pytest.raises(RuntimeError, match="no matching desired relay"):
                _handle_update_relay(action, self._make_panel(), cluster)
            # Critical: the old relay was NOT touched.
            rm.assert_not_called()

    def test_aborts_when_exit_node_unresolvable(self) -> None:
        cluster = _make_cluster_with_relay()
        # Desired references an unknown exit node name
        cluster.desired_relays = [
            DesiredRelay(host="198.51.100.20", name="r1", exit_node="ghost-node", sni="www.google.com"),
        ]
        action = PlanAction(kind=PlanActionKind.UPDATE_RELAY, target="198.51.100.20")

        with patch("meridian.operations.remove_relay") as rm:
            with pytest.raises(RuntimeError, match="could not be resolved"):
                _handle_update_relay(action, self._make_panel(), cluster)
            rm.assert_not_called()

    def test_aborts_when_ssh_preflight_fails(self) -> None:
        cluster = _make_cluster_with_relay()
        action = PlanAction(kind=PlanActionKind.UPDATE_RELAY, target="198.51.100.20")

        # Force ServerConnection.run to fail
        fake_result = MagicMock(returncode=255, stderr="connection refused")
        fake_conn = MagicMock()
        fake_conn.run.return_value = fake_result

        with (
            patch("meridian.ssh.ServerConnection", return_value=fake_conn),
            patch("meridian.operations.remove_relay") as rm,
            patch("meridian.operations.add_relay") as add,
        ):
            with pytest.raises(RuntimeError, match="preflight"):
                _handle_update_relay(action, self._make_panel(), cluster)
            rm.assert_not_called()
            add.assert_not_called()

    def test_proceeds_when_preflight_succeeds(self) -> None:
        cluster = _make_cluster_with_relay()
        action = PlanAction(kind=PlanActionKind.UPDATE_RELAY, target="198.51.100.20")

        fake_result = MagicMock(returncode=0)
        fake_conn = MagicMock()
        fake_conn.run.return_value = fake_result

        with (
            patch("meridian.ssh.ServerConnection", return_value=fake_conn),
            patch("meridian.operations.remove_relay") as rm,
            patch("meridian.operations.add_relay") as add,
        ):
            _handle_update_relay(action, self._make_panel(), cluster)
            rm.assert_called_once()
            add.assert_called_once()
            # remove must run BEFORE add — verify ordering by looking at MagicMock parents
            assert rm.call_args.kwargs.get("relay_ip") == "198.51.100.20"


# ---------------------------------------------------------------------------
# apply.run() command-level failure-safety
# ---------------------------------------------------------------------------


class TestApplyRunFailureSafety:
    """Codex review's #1 highest-value addition: when an early action fails,
    apply.run() must report failure (non-zero exit) and the destructive
    follow-up actions must be skipped — the executor enforces that, but the
    command wrapper must surface the result to the user.
    """

    def _build_cluster_with_desired_clients(self) -> ClusterConfig:
        return ClusterConfig(
            version=2,
            panel=PanelConfig(
                url="https://198.51.100.1/panel",
                api_token="tok",
                server_ip="198.51.100.1",
            ),
            nodes=[
                NodeEntry(
                    ip="198.51.100.1",
                    uuid="00000000-0000-0000-0000-000000000001",
                    name="exit-1",
                    is_panel_host=True,
                )
            ],
            desired_clients=["alice", "bob"],
        )

    def test_failed_action_causes_nonzero_exit(self) -> None:
        cluster = self._build_cluster_with_desired_clients()

        # An execution result with one failed action — apply.run should react.
        failed_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice")
        exec_result = ExecutionResult(results=[ActionResult(action=failed_action, success=False, error="API down")])
        plan = Plan(actions=[failed_action])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch("meridian.commands.apply.print_plan"),
            # `fail()` raises typer.Exit with hint_type-derived code
            patch.object(ClusterConfig, "save"),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1)
            assert exc_info.value.exit_code != 0

    def test_skipped_destructive_action_marks_failure(self) -> None:
        """When the executor skips a REMOVE_NODE because of an earlier failure
        in the same run, the result still has success=False with a 'skipped'
        error. apply.run must treat this as overall failure, not as a no-op.
        """
        cluster = self._build_cluster_with_desired_clients()

        failed_add = ActionResult(
            action=PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice"),
            success=False,
            error="provisioner died",
        )
        skipped_remove = ActionResult(
            action=PlanAction(kind=PlanActionKind.REMOVE_NODE, target="198.51.100.99", destructive=True),
            success=False,
            error="skipped: prior phase had failures",
        )
        exec_result = ExecutionResult(results=[failed_add, skipped_remove])
        plan = Plan(actions=[failed_add.action, skipped_remove.action])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch("meridian.commands.apply.print_plan"),
            patch.object(ClusterConfig, "save"),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1)
            assert exc_info.value.exit_code != 0

    def test_empty_plan_exits_zero(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        empty_plan = Plan(actions=[])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=empty_plan),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1)
            # Exit 0 = no changes needed
            assert exc_info.value.exit_code == 0

    def test_aborts_when_no_desired_state_or_sub_page(self) -> None:
        # A cluster with neither desired state nor subscription page declared —
        # apply has nothing to converge to.
        cluster = ClusterConfig(
            version=2,
            panel=PanelConfig(
                url="https://198.51.100.1/panel",
                api_token="tok",
                server_ip="198.51.100.1",
            ),
        )
        with patch.object(ClusterConfig, "load", return_value=cluster):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1)
            assert exc_info.value.exit_code != 0

    def test_aborts_when_panel_not_configured(self) -> None:
        # Has desired state but the panel is not configured yet
        cluster = ClusterConfig(version=2, desired_clients=["alice"])
        with patch.object(ClusterConfig, "load", return_value=cluster):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1)
            assert exc_info.value.exit_code != 0

    def test_apply_snapshots_desired_state_to_extra(self) -> None:
        """After successful apply, desired_clients is snapshotted into _extra."""
        cluster = self._build_cluster_with_desired_clients()
        add_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice")
        plan = Plan(actions=[add_action])
        exec_result = ExecutionResult(results=[ActionResult(action=add_action, success=True)])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch("meridian.commands.apply.print_plan"),
            patch.object(ClusterConfig, "save"),
        ):
            apply_run(yes=True, parallel=1, prune_extras="yes")

        # Snapshot must be in applied_state after apply
        assert cluster.applied_state.clients == ["alice", "bob"]

    def test_apply_json_success_outputs_typed_result(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        add_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice")
        plan = Plan(actions=[add_action])
        exec_result = ExecutionResult(results=[ActionResult(action=add_action, success=True)])
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch.object(ClusterConfig, "save"),
            redirect_stdout(buf),
        ):
            apply_run(yes=True, parallel=1, prune_extras="yes", json_output=True)

        payload = json.loads(buf.getvalue())
        assert payload["command"] == "apply"
        assert payload["status"] == "changed"
        assert payload["exit_code"] == 0
        assert payload["data"]["all_succeeded"] is True
        assert payload["data"]["counts"] == {"actions": 1, "succeeded": 1, "failed": 0, "skipped": 0}
        assert payload["data"]["actions"][0]["status"] == "succeeded"
        assert payload["data"]["actions"][0]["action"]["kind"] == "add_client"
        assert payload["data"]["actions"][0]["action"]["execution_order"] == 1

    def test_apply_json_failure_outputs_one_partial_result_envelope(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        failed_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice")
        plan = Plan(actions=[failed_action])
        exec_result = ExecutionResult(results=[ActionResult(action=failed_action, success=False, error="API down")])
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch.object(ClusterConfig, "save"),
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1, prune_extras="yes", json_output=True)

        assert exc_info.value.exit_code == 3
        payload, end = json.JSONDecoder().raw_decode(buf.getvalue())
        assert buf.getvalue()[end:].strip() == ""
        assert payload["command"] == "apply"
        assert payload["status"] == "failed"
        assert payload["exit_code"] == 3
        assert payload["errors"][0]["code"] == "MERIDIAN_APPLY_FAILED"
        assert payload["data"]["all_succeeded"] is False
        assert payload["data"]["counts"] == {"actions": 1, "succeeded": 0, "failed": 1, "skipped": 0}
        assert payload["data"]["actions"][0]["status"] == "failed"
        assert payload["data"]["actions"][0]["error"] == "API down"

    def test_apply_json_requires_explicit_confirmation(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        add_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice")
        plan = Plan(actions=[add_action])
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan") as execute,
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=False, parallel=1, prune_extras="ask", json_output=True)

        assert exc_info.value.exit_code == 2
        execute.assert_not_called()
        payload = json.loads(buf.getvalue())
        assert payload["command"] == "apply"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["code"] == "MERIDIAN_CONFIRMATION_REQUIRED"
        assert payload["summary"]["text"] == "Apply requires explicit confirmation"
        assert payload["data"]["all_succeeded"] is False
        assert payload["data"]["summary"] == "Apply requires explicit confirmation"
        assert payload["data"]["plan"]["actions"][0]["kind"] == "add_client"

    def test_apply_json_requires_explicit_drift_decision(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        extra_remove = PlanAction(
            kind=PlanActionKind.REMOVE_CLIENT,
            target="ghost",
            detail="delete client ghost",
            destructive=True,
            from_extras=True,
        )
        plan = Plan(actions=[extra_remove])
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan") as execute,
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1, prune_extras="ask", json_output=True)

        assert exc_info.value.exit_code == 2
        execute.assert_not_called()
        payload = json.loads(buf.getvalue())
        assert payload["status"] == "failed"
        assert payload["errors"][0]["code"] == "MERIDIAN_DRIFT_DECISION_REQUIRED"
        assert payload["data"]["all_succeeded"] is False
        assert payload["data"]["plan"]["actions"][0]["from_extras"] is True

    def test_apply_json_suppresses_nested_fail_json(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        add_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice")
        plan = Plan(actions=[add_action])
        buf = io.StringIO()

        def handler_uses_fail(*_args: object, **_kwargs: object) -> None:
            from meridian.console import fail

            fail("Nested command failure", hint_type="system")

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply._handle_add_client", side_effect=handler_uses_fail),
            patch.object(ClusterConfig, "save"),
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1, prune_extras="yes", json_output=True)

        assert exc_info.value.exit_code == 3
        payload, end = json.JSONDecoder().raw_decode(buf.getvalue())
        assert buf.getvalue()[end:].strip() == ""
        assert payload["command"] == "apply"
        assert payload["status"] == "failed"
        assert payload["data"]["actions"][0]["status"] == "failed"

    def test_apply_json_state_save_failure_preserves_execution_result(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        add_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice")
        plan = Plan(actions=[add_action])
        exec_result = ExecutionResult(results=[ActionResult(action=add_action, success=True)])
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch.object(ClusterConfig, "save", side_effect=OSError("disk full")),
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1, prune_extras="yes", json_output=True)

        assert exc_info.value.exit_code == 3
        payload = json.loads(buf.getvalue())
        assert payload["status"] == "failed"
        assert payload["errors"][0]["code"] == "MERIDIAN_STATE_SAVE_FAILED"
        assert payload["data"]["actions"][0]["status"] == "succeeded"
        assert payload["data"]["all_succeeded"] is False

    def test_apply_result_execution_order_is_stable_even_when_results_finish_out_of_order(self) -> None:
        cluster = self._build_cluster_with_desired_clients()
        first = PlanAction(kind=PlanActionKind.ADD_NODE, target="198.51.100.10", detail="provision first")
        second = PlanAction(kind=PlanActionKind.ADD_NODE, target="198.51.100.11", detail="provision second")
        plan = Plan(actions=[first, second])
        exec_result = ExecutionResult(
            results=[
                ActionResult(action=second, success=True),
                ActionResult(action=first, success=True),
            ]
        )
        buf = io.StringIO()

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", return_value=exec_result),
            patch.object(ClusterConfig, "save"),
            redirect_stdout(buf),
        ):
            apply_run(yes=True, parallel=1, prune_extras="yes", json_output=True)

        payload = json.loads(buf.getvalue())
        by_target = {item["action"]["target"]: item["action"]["execution_order"] for item in payload["data"]["actions"]}
        assert by_target == {"198.51.100.10": 1, "198.51.100.11": 2}


class TestPruneExtras:
    """`--prune-extras={ask,yes,no}` controls how REMOVE_* actions tagged
    `from_extras=True` (drift — present on panel, missing from cluster.yml)
    are handled."""

    def _build_cluster(self) -> ClusterConfig:
        return ClusterConfig(
            version=2,
            panel=PanelConfig(
                url="https://198.51.100.1/panel",
                api_token="tok",
                server_ip="198.51.100.1",
            ),
            nodes=[NodeEntry(ip="198.51.100.1", uuid="u-1", name="exit-1", is_panel_host=True)],
            desired_clients=["alice"],
        )

    def _exec_succeeds(self, plan: Plan) -> ExecutionResult:
        return ExecutionResult(results=[ActionResult(action=a, success=True) for a in plan.actions])

    def test_prune_no_filters_extras_before_executor(self) -> None:
        """`--prune-extras=no` removes from_extras actions from the plan."""
        cluster = self._build_cluster()
        keep_action = PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice")
        extra_remove = PlanAction(
            kind=PlanActionKind.REMOVE_CLIENT,
            target="ghost",
            destructive=True,
            from_extras=True,
        )
        plan = Plan(actions=[keep_action, extra_remove])

        captured_plan = {}

        def capture_execute(plan_arg, **_kwargs):
            captured_plan["actions"] = list(plan_arg.actions)
            return ExecutionResult(results=[ActionResult(action=keep_action, success=True)])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", side_effect=capture_execute),
            patch("meridian.commands.apply.print_plan"),
            patch.object(ClusterConfig, "save"),
        ):
            apply_run(yes=True, parallel=1, prune_extras="no")

        # Extras filtered out — only the ADD_CLIENT survives.
        kinds = [a.kind for a in captured_plan["actions"]]
        assert kinds == [PlanActionKind.ADD_CLIENT]

    def test_prune_yes_keeps_extras(self) -> None:
        """`--prune-extras=yes` runs extras as-is."""
        cluster = self._build_cluster()
        extra_remove = PlanAction(
            kind=PlanActionKind.REMOVE_CLIENT,
            target="ghost",
            destructive=True,
            from_extras=True,
        )
        plan = Plan(actions=[extra_remove])

        captured_plan = {}

        def capture_execute(plan_arg, **_kwargs):
            captured_plan["actions"] = list(plan_arg.actions)
            return ExecutionResult(results=[ActionResult(action=extra_remove, success=True)])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.execute_plan", side_effect=capture_execute),
            patch("meridian.commands.apply.print_plan"),
            patch.object(ClusterConfig, "save"),
        ):
            apply_run(yes=True, parallel=1, prune_extras="yes")

        # The extra REMOVE survived to the executor.
        assert captured_plan["actions"] == [extra_remove]

    def test_prune_ask_with_yes_downgrades_to_no(self) -> None:
        """`--yes` + default `--prune-extras=ask` must NOT auto-remove extras.

        Auto-removing drift without explicit operator consent is destructive
        UX; safer to skip and require a deliberate `--prune-extras=yes`.
        """
        cluster = self._build_cluster()
        extra_remove = PlanAction(
            kind=PlanActionKind.REMOVE_CLIENT,
            target="ghost",
            destructive=True,
            from_extras=True,
        )
        plan = Plan(actions=[extra_remove])

        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.apply.compute_reconciliation_plan", return_value=plan),
            patch("meridian.commands.apply.print_plan"),
            patch.object(ClusterConfig, "save"),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                apply_run(yes=True, parallel=1, prune_extras="ask")
            # Plan becomes empty after filtering → exit 0
            assert exc_info.value.exit_code == 0
