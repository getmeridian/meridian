"""Unavailable profile evidence preserves plans and permits only proven bootstrap or repair."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ActionCheckpoint, ClusterConfig, ManagedResourceBinding, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ResourcePlan, ServiceSettingsPayload
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceNotFound
from meridian.control.models import ServiceSettings, UserCreate
from meridian.core.errors import LocalStateError
from meridian.core.topology import AccessProfileIntent, SetupIntent
from meridian.reconciler.resources import ResourceExecutionResult, build_resource_actions
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.fleet_document import dump_fleet_document, fleet_document
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def command_modes():
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def review(plan: ResourcePlan) -> SimpleNamespace:
    return SimpleNamespace(plan=plan, review_hash="a" * 64, retiring=[])


def inspection(plan: ResourcePlan) -> SimpleNamespace:
    return SimpleNamespace(
        plan_hash=plan.plan_hash,
        review_hash="a" * 64,
        converged=True,
        retiring=[],
        fence_warning="",
        inspections=[
            SimpleNamespace(action=action, error="", converged=True) for action in build_resource_actions(plan, 1)
        ],
    )


@pytest.mark.parametrize("profiles", [False, True])
@pytest.mark.parametrize("outage", ["stopped", "not-bootstrapped", "missing-credentials"])
@pytest.mark.parametrize("json_output", [False, True])
def test_plan_keeps_compiled_results_when_profile_evidence_is_unavailable(
    tmp_home: Path, profiles: bool, outage: str, json_output: bool
):
    intent = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=[])] if profiles else [])
    plan = compile_topology(intent)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN),
        topology_intent=intent,
        active_generation=1,
        active_plan_hash=plan.plan_hash,
    )
    if outage == "missing-credentials":
        cluster.panel = PanelConfig()
    error = (
        ControlServiceNotFound("The service is not installed.", category="user")
        if outage == "not-bootstrapped"
        else ControlServiceError("The control service cannot be reached.", retryable=True)
    )
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.setup.runtime.SetupRuntime") as planner,
        patch("meridian.setup.profile_review.ControlClient") as client,
    ):
        planner.return_value.inspect_intent.return_value = inspection(plan)
        planner.return_value.review.return_value = review(plan)
        if outage == "missing-credentials":
            planner.return_value.inspect_intent.side_effect = LocalStateError(
                "Saved control-service credentials are missing."
            )
        client.return_value.__enter__.return_value.access_profiles.side_effect = error
        client.return_value.__enter__.return_value.preview_access_profiles.side_effect = error
        reply = CliRunner().invoke(app, ["plan", *(["--json"] if json_output else [])])
    assert reply.exit_code == 3, reply.output
    if json_output:
        body = json.loads(reply.stdout)
        assert body["errors"][0]["code"] == "MERIDIAN_PLAN_EVIDENCE_UNAVAILABLE"
        assert body["errors"][0]["retryable"] is True
        data = body["data"]
        assert data["plan_hash"] == plan.plan_hash and data["converged"] is False
        assert [row["logical_id"] for row in data["resources"]] == [row.logical_id for row in plan.resources]
        assert any("profile" in row["error"].lower() for row in data["observation_errors"])
    else:
        text = unstyle(reply.output)
        assert "unavailable" in text.lower() and "Topology" in text
        assert "Traceback" not in text


@pytest.mark.parametrize(
    "saved", ["fresh", "pending-bootstrap", "pending-settings", "committed-plan", "committed-settings"]
)
def test_apply_without_profiles_can_bootstrap_or_repair_with_unavailable_service(tmp_home: Path, saved: str):
    intent = single_exit_intent()
    plan = compile_topology(intent)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN), topology_intent=intent
    )
    if saved.startswith("pending"):
        cluster.pending_generation = 1
        cluster.pending_plan_hash = plan.plan_hash if saved == "pending-bootstrap" else "d" * 64
        first = plan.resources[0]
        cluster.managed_bindings[first.logical_id + "@1"] = ManagedResourceBinding(
            logical_id=first.logical_id, generation=1, resource_kind=first.payload.kind
        )
        if saved == "pending-settings":
            settings = next(row for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload))
            cluster.action_checkpoints["settings"] = ActionCheckpoint(
                resource_id=settings.logical_id, generation=1, expected_hash=settings.desired_hash, status="skipped"
            )
    elif saved.startswith("committed"):
        cluster.active_generation = 1
        cluster.active_plan_hash = plan.plan_hash if saved == "committed-plan" else "b" * 64
        settings = next(row for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload))
        cluster.managed_bindings[settings.logical_id + "@1"] = ManagedResourceBinding(
            logical_id=settings.logical_id,
            generation=1,
            resource_kind=settings.payload.kind,
            desired_hash=settings.desired_hash,
            active=True,
        )
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.v4_topology.reviewed_runtime") as runtime,
        patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        patch("meridian.setup.profile_review.ControlClient") as client,
    ):
        runtime.return_value.review.return_value = review(plan)
        runtime.return_value.apply_intent.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)
        live = client.return_value.__enter__.return_value
        live.access_profiles.side_effect = ControlServiceError("The control service cannot be reached.", retryable=True)
        reply = CliRunner().invoke(app, ["apply", "--yes", "--json"])
        assert reply.exit_code == 0, reply.output
        runtime.return_value.apply_intent.assert_called_once()
        live.preview_access_profiles.assert_not_called()
        assert runtime.call_args.kwargs["access_review_hash"] is None


@pytest.mark.parametrize("saved", ["profiles", "unknown", "pending-profiles"])
@pytest.mark.parametrize("credentials", [False, True])
@pytest.mark.parametrize("used", [False, True], ids=["never-used", "ever-used"])
def test_apply_refuses_unavailable_profile_review_only_once_profiles_have_been_used(
    tmp_home: Path, saved: str, credentials: bool, used: bool
):
    intent = single_exit_intent()
    plan = compile_topology(intent)
    old = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=[])])
    old_plan = compile_topology(old)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN) if credentials else PanelConfig(),
        topology_intent=intent,
        active_generation=1,
        active_plan_hash=old_plan.plan_hash if saved == "profiles" else "c" * 64,
    )
    cluster.access_profiles_used = used
    if saved == "profiles":
        settings = next(row for row in old_plan.resources if isinstance(row.payload, ServiceSettingsPayload))
        cluster.managed_bindings[settings.logical_id + "@1"] = ManagedResourceBinding(
            logical_id=settings.logical_id,
            generation=1,
            resource_kind=settings.payload.kind,
            desired_hash=settings.desired_hash,
            active=True,
        )
    elif saved == "pending-profiles":
        cluster.active_plan_hash = plan.plan_hash
        cluster.pending_generation = 2
        cluster.pending_plan_hash = old_plan.plan_hash
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.v4_topology.reviewed_runtime") as runtime,
        patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        patch("meridian.setup.profile_review.ControlClient") as client,
    ):
        runtime.return_value.review.return_value = review(plan)
        client.return_value.__enter__.return_value.access_profiles.side_effect = ControlServiceError(
            "The control service cannot be reached.", retryable=True
        )
        runtime.return_value.apply_intent.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)
        reply = CliRunner().invoke(app, ["apply", "--yes", "--json"])
        if used:
            assert reply.exit_code == 3, reply.output
            body = json.loads(reply.stdout)
            assert "profile" in body["errors"][0]["message"].lower()
            assert "retry" in body["errors"][0]["hint"].lower()
            assert body["errors"][0]["retryable"] is True
            runtime.return_value.apply_intent.assert_not_called()
        else:
            assert reply.exit_code == 0, reply.output
            runtime.return_value.apply_intent.assert_called_once()
            assert runtime.call_args.kwargs["access_review_hash"] is None


@pytest.mark.parametrize("used", [False, True], ids=["never-used", "ever-used"])
@pytest.mark.parametrize("credentials", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_fleet_import_uses_the_same_profile_outage_gate(
    tmp_path: Path, tmp_home: Path, used: bool, credentials: bool, dry_run: bool
):
    from meridian import config

    intent = single_exit_intent()
    updated = SetupIntent.model_validate({**intent.to_data(), "delivery": {"client_routing": "full"}})
    plan = compile_topology(updated)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN) if credentials else PanelConfig(),
        topology_intent=intent,
    )
    cluster.access_profiles_used = used
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    source = tmp_path / "fleet.yml"
    source.write_text(dump_fleet_document(fleet_document(updated, registry)), encoding="utf-8")
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.fleet_transfer.reviewed_runtime") as runtime,
        patch("meridian.commands.fleet_transfer.forget_unreachable_servers"),
        patch("meridian.commands.fleet_transfer.apply_reviewed_intent") as apply,
        patch("meridian.setup.profile_review.ControlClient") as client,
    ):
        runtime.return_value.review.return_value = review(plan)
        apply.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)
        client.return_value.__enter__.return_value.access_profiles.side_effect = ControlServiceError(
            "The control service cannot be reached.", retryable=True
        )
        reply = CliRunner().invoke(
            app, ["fleet", "import", str(source), "--yes", "--json", *(["--dry-run"] if dry_run else [])]
        )
        if used or dry_run:
            assert reply.exit_code == 3, reply.output
            error = json.loads(reply.stdout)["errors"][0]
            assert error["retryable"] is True and "retry" in error["hint"].lower()
            apply.assert_not_called()
        else:
            assert reply.exit_code == 0, reply.output
            apply.assert_called_once()
            assert apply.call_args.kwargs["access_review_hash"] is None


@pytest.mark.parametrize("in_use", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_fleet_import_reviews_service_profiles_when_both_intents_have_none(
    tmp_path: Path, tmp_home: Path, in_use: bool, dry_run: bool
):
    from meridian import config

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username="person", profiles=["limited"] if in_use else ["default"]))
        intent = single_exit_intent()
        updated = SetupIntent.model_validate({**intent.to_data(), "delivery": {"client_routing": "full"}})
        plan = compile_topology(updated)
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
        registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
        source = tmp_path / "fleet.yml"
        source.write_text(dump_fleet_document(fleet_document(updated, registry)), encoding="utf-8")
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.fleet_transfer.reviewed_runtime") as runtime,
            patch("meridian.commands.fleet_transfer.forget_unreachable_servers"),
            patch("meridian.commands.fleet_transfer.apply_reviewed_intent") as apply,
        ):
            runtime.return_value.review.return_value = review(plan)
            apply.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)
            reply = CliRunner().invoke(
                app, ["fleet", "import", str(source), "--yes", "--json", *(["--dry-run"] if dry_run else [])]
            )
            if in_use:
                assert reply.exit_code == 2 and "used by 1 person" in reply.output, reply.output
                apply.assert_not_called()
            else:
                assert reply.exit_code == 0, reply.output
                data = json.loads(reply.stdout)["data"]
                assert data["access_review"]["access_profiles"] == {}
                if dry_run:
                    apply.assert_not_called()
                else:
                    assert apply.call_args.kwargs["access_review_hash"] == data["access_review"]["access_hash"]
