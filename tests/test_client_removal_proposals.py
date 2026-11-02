"""A removal draft becomes one reviewed intent edit, with a revision guard at apply."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode
from meridian.control.models import UserCreate
from meridian.core.agent_types import canonical_sha256
from meridian.core.output import command_envelope
from meridian.core.people_bulk import ClientBulkResult
from meridian.core.people_removal import PeopleRemovalProposal
from meridian.core.topology import SetupIntent
from meridian.reconciler.fence import FleetFenceError, local_fence_view
from meridian.service_fence import ControlServiceFence
from tests.support.fence_host import FenceHost
from tests.support.people_proposals import commit_topology
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent
from tests.test_client_bulk_commands import isolated_cli_state as isolated_cli_state
from tests.test_control_people_bulk import commit, preview
from tests.test_service_fence import service as service


def test_removal_help_exposes_the_proposal_and_review_options():
    result = CliRunner().invoke(app, ["client", "remove", "--help"])
    assert result.exit_code == 0
    assert "--proposal" in unstyle(result.output) and "--dry-run" in unstyle(result.output)


@pytest.fixture
def removal_cli(service, tmp_home):
    remote = service
    service = remote.service
    intent = single_exit_intent(users=["sample-a", "sample-b", "sample-kept"])
    for name in intent.access.users:
        service.create_user(UserCreate(username=name))
    revision = commit_topology(service, intent)
    cluster = ClusterConfig(
        fleet_id=service.fleet_id,
        active_generation=1,
        active_plan_hash=revision.plan_hash,
        panel=PanelConfig(url=remote.url, api_token="admin-token-for-tests-only-0123456789"),
        topology_intent=intent,
    )
    yield remote, cluster
    set_json_mode(False)


def review_runtime():
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(review_hash="f" * 64, retiring=[])
    return runtime


@pytest.fixture(params=["casefold", "display_name"])
def colliding_removal(removal_cli, request):
    remote, cluster = removal_cli
    console_name = "SAMPLE-A" if request.param == "casefold" else "sample-display"
    data = cluster.topology_intent.model_dump(mode="json")
    if request.param == "display_name":
        data["access"]["display_names"] = {"sample-a": console_name}
    cluster.topology_intent = SetupIntent.model_validate(data)
    commit_topology(remote.service, cluster.topology_intent)
    remote.service.create_user(UserCreate(username=console_name))
    proposal = preview(remote.service, "delete", names=["sample-a"]).proposal
    forged = PeopleRemovalProposal.model_validate({**proposal.to_data(), "usernames": [console_name]})
    return remote, cluster, forged


def test_proposal_usernames_cannot_resolve_to_another_declared_person(colliding_removal, tmp_path):
    _, cluster, proposal = colliding_removal
    path = tmp_path / "removal.json"
    path.write_text(proposal.model_dump_json(by_alias=True))
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.client_removal.reviewed_runtime", return_value=review_runtime()) as runtime,
        patch("meridian.commands.client_removal.apply_reviewed_intent") as apply,
        patch.object(ClusterConfig, "save") as save,
    ):
        result = CliRunner().invoke(app, ["client", "remove", "--proposal", str(path), "--yes", "--json"])
    assert result.exit_code == 2, result.output
    assert "not declared in the topology" in json.loads(result.stdout)["errors"][0]["message"]
    runtime.assert_not_called()
    apply.assert_not_called()
    save.assert_not_called()


def test_current_removal_requires_exact_declared_usernames(colliding_removal):
    from meridian.commands.client_removal import require_current_removal

    _, cluster, proposal = colliding_removal
    with pytest.raises(FleetFenceError, match="not declared in the topology"):
        require_current_removal(cluster, proposal)


@pytest.mark.parametrize("action", ["ensure", "acquire"])
def test_removal_fence_requires_exact_committed_usernames(colliding_removal, action):
    remote, cluster, proposal = colliding_removal
    fence = ControlServiceFence(
        FenceHost(), cluster, client_factory=remote.client, cli_version="4.0.0", removal_proposal=proposal
    )
    before = remote.service.revisions.fence()
    with pytest.raises(FleetFenceError, match="not declared in the topology"):
        getattr(fence, action)(local_fence_view(cluster))
    assert remote.service.revisions.fence() == before


def test_removal_fence_reads_the_committed_intent_after_the_local_removal_is_saved(removal_cli):
    from meridian.setup.editor import remove_access_user_from_intent

    remote, cluster = removal_cli
    proposal = preview(remote.service, "delete", names=["sample-a"]).proposal
    fence = ControlServiceFence(
        FenceHost(), cluster, client_factory=remote.client, cli_version="4.0.0", removal_proposal=proposal
    )
    local = local_fence_view(cluster)
    fence.ensure(local)
    cluster.topology_intent = remove_access_user_from_intent(cluster.topology_intent, "sample-a")
    try:
        assert fence.acquire(local) == cluster.fleet_id
    finally:
        fence.release()


def test_multiple_names_are_one_dry_run_review_and_do_not_save_intent(removal_cli):
    _, cluster = removal_cli
    runtime = review_runtime()
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.client_removal.reviewed_runtime", return_value=runtime),
        patch("meridian.commands.client_removal.apply_reviewed_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["client", "remove", "sample-a", "sample-b", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert runtime.review.call_count == 1
    assert runtime.review.call_args.args[0].to_intent().access.users == ["sample-kept"]
    assert json.loads(result.stdout)["data"]["applied"] is False
    assert cluster.topology_intent.access.users == ["sample-a", "sample-b", "sample-kept"]
    apply.assert_not_called()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_a_bulk_result_file_applies_its_proposal_as_one_review_confirmation_and_apply(removal_cli, tmp_path, locale):
    remote, cluster = removal_cli
    proposal = preview(remote.service, "delete", names=["sample-a", "sample-b"]).proposal
    path = tmp_path / "removal.json"
    path.write_text(proposal.model_dump_json(by_alias=True))
    runtime = review_runtime()
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.client_removal.reviewed_runtime", return_value=runtime),
        patch("meridian.commands.client_removal.confirm", return_value=True) as confirm,
        patch(
            "meridian.commands.client_removal.apply_reviewed_intent", return_value=SimpleNamespace(changed=True)
        ) as apply,
    ):
        result = CliRunner().invoke(app, ["--lang", locale, "client", "remove", "--proposal", str(path)])
    assert result.exit_code == 0, result.output
    assert runtime.review.call_count == confirm.call_count == apply.call_count == 1
    assert apply.call_args.args[2].access.users == ["sample-kept"]
    assert apply.call_args.kwargs["expected_plan_hash"] == "f" * 64
    assert apply.call_args.kwargs["removal_proposal"] == proposal


@pytest.mark.parametrize("field", ["fleet_id", "revision", "generation", "plan_hash", "intent_sha256"])
def test_a_foreign_or_stale_proposal_stops_before_review_or_apply(removal_cli, tmp_path, field):
    remote, cluster = removal_cli
    proposal = preview(remote.service, "delete", names=["sample-a", "sample-b"]).proposal
    data = proposal.to_data()
    data[field] = (
        "00000000-0000-4000-8000-000000000001"
        if field == "fleet_id"
        else 9
        if field in ("revision", "generation")
        else "c" * 64
    )
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(data))
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.client_removal.reviewed_runtime") as runtime,
        patch("meridian.commands.client_removal.apply_reviewed_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["client", "remove", "--proposal", str(path), "--yes", "--json"])
    assert result.exit_code == 2, result.output
    runtime.assert_not_called()
    apply.assert_not_called()


def test_the_fence_refuses_a_new_revision_even_when_generation_and_plan_did_not_change(removal_cli):
    remote, cluster = removal_cli
    proposal = preview(remote.service, "delete", names=["sample-a", "sample-b"]).proposal
    fence = ControlServiceFence(
        FenceHost(),
        cluster,
        client_factory=remote.client,
        cli_version="4.0.0",
        removal_proposal=proposal,
    )
    local = local_fence_view(cluster)
    fence.ensure(local)
    commit_topology(remote.service, cluster.topology_intent)
    before = remote.service.revisions.fence()
    with pytest.raises(FleetFenceError):
        fence.acquire(local)
    assert remote.service.revisions.fence() == before
    assert proposal.intent_sha256 == canonical_sha256(cluster.topology_intent.model_dump(mode="json"))


@pytest.mark.parametrize("shape", ["api_preview", "api_receipt", "cli_preview", "cli_receipt"])
def test_proposal_reader_accepts_bulk_results_without_copying_person_fields(removal_cli, tmp_path, shape):
    from meridian.commands.client_removal import read_removal_proposal

    remote, _ = removal_cli
    review = preview(remote.service, "delete", names=["sample-a", "sample-b"])
    receipt = commit(remote.service, review)
    if shape.startswith("cli"):
        result = ClientBulkResult(
            preview=review if shape == "cli_preview" else None,
            receipt=receipt if shape == "cli_receipt" else None,
            applied=shape == "cli_receipt",
        )
        body = command_envelope(command="client.bulk.delete", data=result.to_data()).to_data()
    else:
        body = (review if shape == "api_preview" else receipt).to_data()
    path = tmp_path / "bulk-result.json"
    path.write_text(json.dumps(body))
    assert read_removal_proposal(str(path)) == review.proposal


@pytest.mark.parametrize("case", ["duplicate", "last", "service_owned", "cut_off", "proposal_cut_off", "declined"])
def test_refused_or_declined_batch_removal_does_not_apply_or_save(removal_cli, tmp_path, case):
    remote, cluster = removal_cli
    args = ["client", "remove", "sample-a", "sample-b"]
    if case == "duplicate":
        args = ["client", "remove", "sample-a", "sample-a"]
    elif case == "last":
        args += ["sample-kept"]
    elif case == "service_owned":
        remote.service.create_user(UserCreate(username="service-only"))
        args += ["service-only"]
    elif case == "cut_off":
        args += ["--cut-off"]
    elif case == "proposal_cut_off":
        proposal = preview(remote.service, "delete", names=["sample-a", "sample-b"]).proposal
        path = tmp_path / "removal.json"
        path.write_text(proposal.model_dump_json(by_alias=True))
        args = ["client", "remove", "--proposal", str(path), "--cut-off"]
    before = cluster.topology_intent
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.client_removal.reviewed_runtime", return_value=review_runtime()),
        patch("meridian.commands.client_removal.confirm", return_value=False),
        patch("meridian.commands.client_removal.apply_reviewed_intent") as apply,
        patch.object(ClusterConfig, "save") as save,
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == (1 if case == "declined" else 2), result.output
    assert cluster.topology_intent == before
    apply.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "malformed", "secret", "no_proposal"])
def test_invalid_proposal_files_stop_before_loading_the_fleet(tmp_path, case):
    path = tmp_path / "invalid.json"
    if case == "malformed":
        path.write_text("{")
    elif case == "secret":
        path.write_text(json.dumps({"usernames": ["sample-a"], "uuid": "sample-private-credential"}))
    elif case == "no_proposal":
        path.write_text(ClientBulkResult(applied=False).model_dump_json())
    try:
        with patch("meridian.commands._helpers.ClusterConfig.load") as load:
            result = CliRunner().invoke(app, ["client", "remove", "--proposal", str(path), "--dry-run", "--json"])
        assert result.exit_code == 2, result.output
        load.assert_not_called()
        assert "sample-private-credential" not in result.output
    finally:
        set_json_mode(False)
