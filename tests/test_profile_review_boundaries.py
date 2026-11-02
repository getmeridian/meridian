"""Profile review survives export and interrupted apply without inventing access changes."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings, UserCreate
from meridian.core.access_profile_views import AccessProfileReviewRequest, GrantedInbound
from meridian.core.topology import SetupIntent
from meridian.reconciler.resources import ResourceExecutionResult
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent
from tests.test_access_profiles import HY2, POLICY, REALITY
from tests.test_access_profiles import service as service


@pytest.fixture(autouse=True)
def command_modes():
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def test_fleet_export_carries_current_creation_membership_without_rewriting_intent(tmp_path: Path, tmp_home: Path):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username="default", profiles=["limited"]))
        intent = SetupIntent.model_validate(
            {**single_exit_intent().to_data(), "access_profiles": [{"id": "limited", "grants": []}]}
        )
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            reply = CliRunner().invoke(app, ["fleet", "export", "--json"])
        set_json_mode(False)
        assert reply.exit_code == 0, reply.output
        document = json.loads(reply.stdout)["data"]["document"]
        assert document["access"]["creation_settings"]["default"]["profiles"] == ["limited"]
        assert cluster.topology_intent.access.creation_settings == {}


def test_apply_rechecks_profile_access_and_passes_the_review_fence_to_the_driver(tmp_path: Path, tmp_home: Path):
    with serve_control(tmp_path) as url:
        intent = SetupIntent.model_validate(
            {**single_exit_intent().to_data(), "access_profiles": [{"id": "limited", "grants": ["exit:exit-a"]}]}
        )
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        review = SimpleNamespace(plan=compile_topology(intent), review_hash="a" * 64, retiring=[])
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.v4_topology.reviewed_runtime") as runtime,
            patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        ):
            runtime.return_value.review.return_value = review
            runtime.return_value.apply_intent.return_value = ResourceExecutionResult(plan_hash="a" * 64, generation=2)
            reply = CliRunner().invoke(app, ["apply", "--yes", "--json"])
        set_json_mode(False)
        assert reply.exit_code == 0, reply.output
        data = json.loads(reply.stdout)["data"]
        assert runtime.call_args.kwargs["access_review_hash"] == data["access_review"]["access_hash"]


def test_first_profile_review_does_not_mistake_reference_metadata_for_new_access(service):
    service.put_user("person")
    context = [
        GrantedInbound(node_id="main", inbound_tag="vless", inbound_ref=REALITY, granted_by=[]),
        GrantedInbound(node_id="main", inbound_tag="hy2", inbound_ref=HY2, granted_by=[]),
    ]
    review = service.access.preview(
        AccessProfileReviewRequest(access_profiles=POLICY, inbound_refs=[REALITY, HY2], existing_inbounds=context)
    )
    assert review.people == [] and review.gained_people == review.lost_people == 0


def test_read_only_reviews_do_not_trigger_post_mutation_backups(tmp_path: Path):
    from meridian.control.client import add_mutation_listener
    from meridian.core.people_bulk import BulkAction, BulkPreviewRequest

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.put_user("person")
        writes = []
        stop = add_mutation_listener(writes.append)
        try:
            client.preview_access_profiles(AccessProfileReviewRequest(access_profiles={}))
            client.preview_bulk(BulkPreviewRequest(usernames=["person"], action=BulkAction(action="disable")))
        finally:
            stop()
        assert writes == []


@pytest.mark.parametrize("in_use", [False, True])
@pytest.mark.parametrize("argv", [["plan", "--json"], ["apply", "--json"], ["apply", "--yes", "--json"]])
def test_removing_the_service_last_profile_is_reviewed_refused_when_used_and_fenced(
    tmp_path: Path, tmp_home: Path, argv: list[str], in_use: bool
):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username="person", profiles=["limited"] if in_use else ["default"]))
        intent = single_exit_intent()
        plan = compile_topology(intent)
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        review = SimpleNamespace(plan=plan, review_hash="a" * 64, retiring=[])
        inspection = SimpleNamespace(
            plan_hash=plan.plan_hash,
            review_hash="a" * 64,
            converged=False,
            retiring=[],
            fence_warning="",
            inspections=[
                SimpleNamespace(action=SimpleNamespace(resource=row), error="", converged=True)
                for row in plan.resources
            ],
        )
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.v4_topology.reviewed_runtime") as runtime,
            patch("meridian.setup.runtime.SetupRuntime") as planner,
            patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        ):
            runtime.return_value.review.return_value = review
            runtime.return_value.apply_intent.return_value = ResourceExecutionResult(
                plan_hash=plan.plan_hash, generation=2
            )
            planner.return_value.inspect_intent.return_value = inspection
            reply = CliRunner().invoke(app, argv)
            set_json_mode(False)
            if in_use:
                assert reply.exit_code == (1 if argv[0] == "plan" else 2), reply.output
                assert "limited" in reply.output and "used by 1 person" in reply.output
                runtime.return_value.apply_intent.assert_not_called()
            else:
                assert reply.exit_code == (0 if "--yes" in argv else 2), reply.output
                data = json.loads(reply.stdout)["data"]
                assert data["access_review"]["access_profiles"] == {}
                assert data["access_review"]["access_hash"]
                if "--yes" in argv:
                    assert runtime.call_args.kwargs["access_review_hash"] == data["access_review"]["access_hash"]
                else:
                    runtime.return_value.apply_intent.assert_not_called()
        assert admin.settings().access_profiles == {"limited": []}
