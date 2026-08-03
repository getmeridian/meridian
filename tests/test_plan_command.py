"""Tests for `meridian plan --json`.

The text-mode plan output is verified by the system lab (Stage 9) and by
TestDisplay in test_reconciler.py. These tests cover the JSON serialization
path used by CI/CD pipelines that need to gate on plan output.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
import typer

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig
from meridian.commands.plan import run as plan_run
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.errors import InfrastructureEvidenceUnavailableError, LocalStateCorruptedError
from meridian.reconciler.diff import ActionChange, Plan, PlanAction, PlanActionKind
from meridian.remnawave import RemnawaveAuthError


def _configured_cluster_with_desired() -> ClusterConfig:
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


def test_v4_plan_routes_through_compiler_graph() -> None:
    from meridian.core.topology import (
        AccessIntent,
        ControlPlaneIntent,
        ExitIntent,
        ProtocolPathIntent,
        SetupIntent,
    )

    cluster = ClusterConfig(
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
    with (
        patch.object(ClusterConfig, "load", return_value=cluster),
        patch("meridian.commands.plan._run_v4_plan") as run_v4,
        patch("meridian.commands.plan.validate_cluster_for_reconciliation") as validate_legacy,
    ):
        plan_run()

    run_v4.assert_called_once()
    validate_legacy.assert_not_called()


def _capture_plan_json(plan: Plan) -> tuple[dict, int]:
    """Run plan command in --json mode against a stubbed plan; return (parsed_json, exit_code)."""
    cluster = _configured_cluster_with_desired()
    buf = io.StringIO()
    with (
        patch.object(ClusterConfig, "load", return_value=cluster),
        patch("meridian.remnawave.MeridianPanel"),
        patch("meridian.ssh.ServerConnection"),
        patch("meridian.commands.plan.compute_reconciliation_plan", return_value=plan),
        redirect_stdout(buf),
    ):
        with pytest.raises(typer.Exit) as exc_info:
            plan_run(json_output=True)
    return json.loads(buf.getvalue()), exc_info.value.exit_code


class TestPlanJsonOutput:
    def test_malformed_cluster_emits_one_typed_envelope(self) -> None:
        buf = io.StringIO()
        error = LocalStateCorruptedError("Cannot safely load cluster.yml", hint="Restore a backup.")

        with (
            patch.object(ClusterConfig, "load", side_effect=error),
            redirect_stdout(buf),
            pytest.raises(typer.Exit) as exc_info,
        ):
            plan_run(json_output=True)

        assert exc_info.value.exit_code == 2
        payload, end = json.JSONDecoder().raw_decode(buf.getvalue())
        assert not buf.getvalue()[end:].strip()
        assert payload["command"] == "plan"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "user"

    def test_empty_plan_emits_converged_true_and_exit_zero(self) -> None:
        payload, exit_code = _capture_plan_json(Plan(actions=[]))
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "plan"
        assert payload["status"] == "no_changes"
        assert payload["summary"]["changed"] is False
        assert payload["summary"]["counts"] == {
            "actions": 0,
            "adds": 0,
            "updates": 0,
            "replacements": 0,
            "removes": 0,
            "destructive": 0,
            "from_extras": 0,
        }
        assert payload["data"]["converged"] is True
        assert payload["data"]["actions"] == []
        assert payload["data"]["exit_code"] == 0
        assert exit_code == 0

    def test_non_empty_plan_emits_actions_and_exit_two(self) -> None:
        plan = Plan(
            actions=[
                PlanAction(kind=PlanActionKind.ADD_CLIENT, target="alice", detail="create client alice"),
                PlanAction(
                    kind=PlanActionKind.REMOVE_CLIENT,
                    target="ghost",
                    detail="delete client ghost",
                    destructive=True,
                    from_extras=True,
                ),
            ]
        )
        payload, exit_code = _capture_plan_json(plan)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "plan"
        assert payload["status"] == "changed"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["converged"] is False
        assert payload["data"]["exit_code"] == 2
        assert exit_code == 2
        assert len(payload["data"]["actions"]) == 2
        # ADD_CLIENT entry
        add = next(a for a in payload["data"]["actions"] if a["kind"] == "add_client")
        assert add["target"] == "alice"
        assert add["plan_index"] == 0
        assert add["operation"] == "add"
        assert add["execution_order"] == 2
        assert add["resource_type"] == "client"
        assert add["resource_id"] == "alice"
        assert add["phase"] == "provision"
        assert add["requires_confirmation"] is False
        assert add["destructive"] is False
        assert add["replacement"] is False
        assert add["replacement_strategy"] == "none"
        assert add["from_extras"] is False
        assert add["destructive_reason"] == ""
        assert add["change_set"] == []
        assert add["symbol"] == "+"
        # REMOVE_CLIENT entry — extras flag preserved for downstream tooling
        rm = next(a for a in payload["data"]["actions"] if a["kind"] == "remove_client")
        assert rm["target"] == "ghost"
        assert rm["plan_index"] == 1
        assert rm["operation"] == "remove"
        assert rm["execution_order"] == 1
        assert rm["resource_type"] == "client"
        assert rm["resource_id"] == "ghost"
        assert rm["phase"] == "deprovision"
        assert rm["requires_confirmation"] is True
        assert rm["destructive"] is True
        assert rm["replacement"] is False
        assert rm["replacement_strategy"] == "none"
        assert rm["destructive_reason"] == "delete client ghost"
        assert rm["from_extras"] is True
        assert rm["symbol"] == "-"

    def test_payload_is_valid_json_with_indentation(self) -> None:
        """Output should be human-readable (indented) AND machine-parseable.

        Some CI scripts pipe to `jq` directly; some `cat` it. Both need
        valid JSON; humans want to read it without `jq`.
        """
        plan = Plan(actions=[PlanAction(kind=PlanActionKind.ADD_CLIENT, target="x")])
        cluster = _configured_cluster_with_desired()
        buf = io.StringIO()
        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.remnawave.MeridianPanel"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.commands.plan.compute_reconciliation_plan", return_value=plan),
            redirect_stdout(buf),
        ):
            with pytest.raises(typer.Exit):
                plan_run(json_output=True)
        raw = buf.getvalue()
        # Indented = contains a newline before the first action key
        assert "\n" in raw
        # Parses as JSON
        json.loads(raw)

    def test_update_plan_action_includes_structured_change_set(self) -> None:
        plan = Plan(
            actions=[
                PlanAction(
                    kind=PlanActionKind.UPDATE_NODE,
                    target="198.51.100.2",
                    detail="redeploy node exit-b: sni: old.example → new.example",
                    changes=[ActionChange("sni", "old.example", "new.example")],
                )
            ]
        )

        payload, exit_code = _capture_plan_json(plan)

        action = payload["data"]["actions"][0]
        assert exit_code == 2
        assert action["kind"] == "update_node"
        assert action["operation"] == "update"
        assert action["change_set"] == [{"field": "sni", "before": "old.example", "after": "new.example"}]

    def test_relay_update_is_explicit_replacement(self) -> None:
        plan = Plan(
            actions=[
                PlanAction(
                    kind=PlanActionKind.UPDATE_RELAY,
                    target="198.51.100.3",
                    detail="update relay relay-a: exit_node: old -> new",
                    destructive=True,
                    changes=[ActionChange("exit_node", "old", "new")],
                )
            ]
        )

        payload, exit_code = _capture_plan_json(plan)

        action = payload["data"]["actions"][0]
        assert exit_code == 2
        assert action["kind"] == "update_relay"
        assert action["operation"] == "replace"
        assert action["phase"] == "replace"
        assert action["replacement"] is True
        assert action["replacement_strategy"] == "delete_then_create"
        assert payload["data"]["counts"]["replacements"] == 1

    def test_panel_auth_failure_is_user_error_json(self) -> None:
        cluster = _configured_cluster_with_desired()
        cluster.panel.url = "https://198.51.100.1/secret-panel"
        buf = io.StringIO()
        set_json_mode(True)
        set_quiet_mode(True)
        try:
            with (
                patch.object(ClusterConfig, "load", return_value=cluster),
                patch("meridian.ssh.ServerConnection"),
                patch("meridian.remnawave.MeridianPanel") as panel_cls,
                redirect_stdout(buf),
            ):
                panel_cls.return_value.__enter__.side_effect = RemnawaveAuthError(
                    "Panel authentication failed",
                    hint="Check your API token.",
                    category="user",
                )
                with pytest.raises(typer.Exit) as exc_info:
                    plan_run(json_output=True)
        finally:
            set_json_mode(False)
            set_quiet_mode(False)

        payload = json.loads(buf.getvalue())
        assert exc_info.value.exit_code == 2
        assert payload["command"] == "plan"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "user"
        assert "secret-panel" not in json.dumps(payload)

    def test_subscription_observation_failure_is_system_error_json(self) -> None:
        cluster = _configured_cluster_with_desired()
        buf = io.StringIO()
        with (
            patch.object(ClusterConfig, "load", return_value=cluster),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.remnawave.MeridianPanel"),
            patch(
                "meridian.commands.plan.compute_reconciliation_plan",
                side_effect=InfrastructureEvidenceUnavailableError(
                    "Could not determine whether the subscription page is running",
                    hint="Restore SSH access and rerun plan.",
                ),
            ),
            redirect_stdout(buf),
            pytest.raises(typer.Exit) as exc_info,
        ):
            plan_run(json_output=True)

        payload = json.loads(buf.getvalue())
        assert exc_info.value.exit_code == 3
        assert payload["command"] == "plan"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "system"
