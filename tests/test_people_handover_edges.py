"""People handover quirks at live service, CLI, and resumable setup boundaries."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from meridian.cli import app
from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler import compile_topology
from meridian.control.client import ControlServiceConflict
from meridian.control.models import UserCreate
from meridian.core.errors import MeridianError
from meridian.core.people_creation import creation_requests
from meridian.core.people_query import PeopleQuery
from meridian.core.people_seeds import PeopleAdoption
from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
from meridian.core.setup import SetupDraft
from meridian.core.token_models import TokenCreate
from meridian.people_adoption import ensure_creation_requests, ensure_people, finish_people_adoption
from meridian.reconciler.resources import ResourceExecutionResult, ResourceInspectionResult
from meridian.servers import ServerRegistry
from meridian.setup.runtime import SetupReview, SetupRuntime
from tests.support.people_cli import committed_people_revision, legacy_name
from tests.support.people_cli import people_cli as people_cli


@pytest.mark.parametrize("present", [False, True], ids=["missing-seed", "backfill-skipped"])
def test_a_taken_legacy_name_never_wedges_the_finish_or_rotates_someone_elses_keys(people_cli, present):
    _runner, cluster, client = people_cli
    other = client.create_user(UserCreate(username="other", display_name="Alex"))
    if present:
        client.create_user(UserCreate(username="sample"))
    cluster.people_adoption = PeopleAdoption(state="committed", users=["sample"], display_names={"sample": "Alex"})
    cluster.save()
    with patch("meridian.console.warn") as warnings:
        finish_people_adoption(cluster)
    warnings.assert_called_once()
    assert "sample" in warnings.call_args.args[0]
    assert client.get_user("sample").display_name == ""
    assert client.get_user("other") == other
    assert ClusterConfig.load().people_adoption is None


@pytest.mark.parametrize("reason", ["user_exists", "another_conflict"])
def test_a_creation_race_is_recognized_by_its_code_even_when_the_message_sounds_like_a_name_clash(people_cli, reason):
    _runner, _cluster, client = people_cli
    before = client.create_user(UserCreate(username="sample"))
    observed = MagicMock(wraps=client)
    observed.get_user.side_effect = [None, before]
    observed.create_user.side_effect = ControlServiceConflict("Each person needs a different name.", reason=reason)
    seeds = PeopleAdoption(users=["sample"])
    if reason == "user_exists":
        assert ensure_people(observed, seeds) == [before]
    else:
        with pytest.raises(ControlServiceConflict):
            ensure_people(observed, seeds)
    assert client.get_user("sample") == before


@pytest.mark.parametrize("command", [["list"], ["show", "sample"]], ids=["list", "show"])
def test_an_unfinished_move_can_warn_but_cannot_hide_a_readable_person(people_cli, command):
    runner, cluster, client = people_cli
    client.create_user(UserCreate(username="sample"))
    cluster.people_adoption = PeopleAdoption(state="committed", users=["sample"])
    cluster.save()
    with patch("meridian.people_adoption.ensure_people", side_effect=MeridianError("Finish unavailable")):
        result = runner.invoke(app, ["client", *command])
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "sample" in result.output and "Finish unavailable" in result.output
    assert ClusterConfig.load().people_adoption is not None


@pytest.mark.parametrize("typed", ["Alice", "ALICE", "Display", "样本"])
def test_setup_resolves_every_seed_even_when_a_latin_alias_is_not_the_username(people_cli, tmp_home, typed):
    _runner, cluster, client = people_cli
    wanted = client.create_user(UserCreate(username="alice", display_name="样本" if typed == "样本" else "Display"))
    seeds = creation_requests([typed])
    assert ensure_creation_requests(client, seeds) == [wanted]
    draft = SetupDraft.from_intent(cluster.topology_intent)
    draft = draft.model_copy(update={"access": draft.access.model_copy(update={"people": seeds})})
    runtime = SetupRuntime(ServerRegistry(tmp_home / "servers.json"), cluster_loader=lambda: cluster)
    assert runtime.resolve_people(draft) == ["alice"]
    assert len(client.list_users()) == 1


@pytest.mark.parametrize("command", ["remove", "reissue", "setup"])
def test_a_historical_alias_twin_is_refused_without_picking_or_writing(people_cli, tmp_home, command):
    runner, _cluster, client = people_cli
    first = client.create_user(UserCreate(username="sample-a", display_name="First"))
    client.create_user(UserCreate(username="sample-b", display_name="Second"))
    legacy_name(tmp_home / "service" / "control.db", "sample-b", "SAMPLE-A")
    before = client.list_users()
    if command == "setup":
        with pytest.raises(MeridianError, match="several people"):
            ensure_creation_requests(client, creation_requests(["Sample-A"]))
        assert ensure_creation_requests(client, creation_requests(["sample-a"])) == [first]
    else:
        result = runner.invoke(app, ["client", command, "Sample-A", "--yes", "--json"])
        assert result.exit_code == 2, result.output
        assert "sample-a" in result.stdout and "sample-b" in result.stdout
    assert client.list_users() == before


@pytest.mark.parametrize("length", [41, 64])
@pytest.mark.parametrize("batch", [False, True], ids=["one-person", "reviewed-batch"])
def test_a_long_username_with_no_display_name_can_be_read_and_removed_after_commit(people_cli, length, batch):
    runner, _cluster, client = people_cli
    name = "u" * length
    client.create_user(UserCreate(username=name))
    result = runner.invoke(app, ["client", "show", name, "--json"])
    assert result.exit_code == 0, result.output + str(result.exception)
    names = [name]
    if batch:
        client.create_user(UserCreate(username="second"))
        names.append("second")
    result = runner.invoke(app, ["client", "remove", *names, "--yes", "--json"])
    assert result.exit_code == 0, result.output + str(result.exception)
    data = json.loads(result.stdout)["data"]
    references = data["clients"] if batch else [data["client"]]
    person = next(person for person in references if person["username"] == name)
    assert "display_name" not in person
    assert not client.list_users()


@pytest.mark.parametrize("names", [["alice", "alice"], ["Alice", " alice "], [""]])
def test_a_duplicate_or_empty_add_batch_never_gets_as_far_as_a_write(people_cli, names):
    runner, _cluster, client = people_cli
    result = runner.invoke(app, ["client", "add", *names, "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "failed"
    assert not client.list_users()


@pytest.mark.parametrize("refusal", ["missing", "needs-yes"])
def test_json_removal_refusals_keep_the_envelope_and_never_prompt(people_cli, refusal):
    runner, _cluster, client = people_cli
    client.create_user(UserCreate(username="sample"))
    args = ["client", "remove", "missing" if refusal == "missing" else "sample", "--json"]
    if refusal == "missing":
        args.append("--yes")
    with patch("meridian.commands.client_v4.confirm", side_effect=AssertionError("Unexpected prompt")):
        result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output + str(result.exception)
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "client.remove" and envelope["status"] == "failed"
    assert envelope["errors"] and envelope["exit_code"] == 2
    assert len(client.list_users()) == 1


def test_saying_no_to_a_batch_removal_leaves_every_frozen_person_untouched(people_cli):
    runner, _cluster, client = people_cli
    before = [client.create_user(UserCreate(username=name)) for name in ["sample-a", "sample-b"]]
    with patch("meridian.commands.client_removal.confirm", return_value=False) as confirm:
        result = runner.invoke(app, ["client", "remove", "sample-a", "sample-b"])
    assert result.exit_code == 1, result.output
    confirm.assert_called_once()
    assert client.list_users() == before


@pytest.mark.parametrize("refusal", ["version", "auth", "scope"])
def test_add_global_refusals_keep_exit_two_and_the_original_hint(people_cli, monkeypatch, refusal):
    runner, cluster, client = people_cli
    if refusal == "version":
        monkeypatch.setattr("meridian.people_adoption.__version__", "99.0.0")
        hint = "meridian apply"
    elif refusal == "auth":
        cluster.panel.api_token = "wrong-token"
        hint = "saved credentials"
    else:
        cluster.panel.api_token = client.issue_token(TokenCreate(name="Reader", scope="read")).token
        hint = "scope"
    cluster.save()
    result = runner.invoke(app, ["client", "add", "sample-a", "sample-b", "--json"])
    assert result.exit_code == 2, result.output + str(result.exception)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "failed" and hint in envelope["errors"][0]["hint"]
    assert not client.list_users()


def test_one_rejected_name_still_reports_the_successes_and_a_failed_json_envelope(people_cli):
    runner, _cluster, client = people_cli
    original = type(client).create_user

    def create(self, request):
        if request.username == "rejected":
            raise ControlServiceConflict("A per-person refusal", reason="name_conflict", category="user")
        return original(self, request)

    with patch("meridian.control.client.ControlClient.create_user", create):
        result = runner.invoke(app, ["client", "add", "sample", "rejected", "--json"])
    assert result.exit_code == 3, result.output + str(result.exception)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "failed" and envelope["errors"]
    assert envelope["summary"]["counts"] == {"added": 1, "failed": 1}
    assert [person.username for person in client.list_users()] == ["sample"]


@pytest.mark.parametrize(
    "args,module",
    [
        (["remove", "sample"], "client_v4"),
        (["reissue", "sample"], "client"),
        (["rotate-credentials", "sample"], "client_credentials"),
        (["set", "sample", "--profile", "default"], "client_profiles"),
        (["routing", "sample", "--inherit"], "client_routing"),
    ],
)
def test_an_old_service_is_refused_before_any_person_confirmation(people_cli, monkeypatch, args, module):
    runner, _cluster, client = people_cli
    before = client.create_user(UserCreate(username="sample", display_name="样本"))
    monkeypatch.setattr("meridian.people_adoption.__version__", "99.0.0")
    monkeypatch.setattr("meridian.console.has_tty", lambda: True)
    target = "meridian.console.confirm" if module == "client_routing" else f"meridian.commands.{module}.confirm"
    with patch(target, side_effect=AssertionError("Unexpected prompt")):
        result = runner.invoke(app, ["client", *args])
    assert result.exit_code == 2, result.output + str(result.exception)
    assert "meridian apply" in result.output
    assert client.get_user("sample") == before


@pytest.mark.parametrize("token", ["people", "read", "explicit-read", "no-revisions"])
def test_pending_recovery_does_not_demand_fleet_rights_from_an_account_reader(people_cli, token):
    runner, cluster, client = people_cli
    client.create_user(UserCreate(username="sample", display_name="样本"))
    request = (
        TokenCreate(name="Scoped reader", capabilities=["people.read"])
        if token == "explicit-read"
        else TokenCreate(name="Scoped reader", capabilities=["people.read", "topology.read"])
        if token == "no-revisions"
        else TokenCreate(name="Scoped reader", scope=token)
    )
    cluster.people_adoption = PeopleAdoption(users=["sample"])
    cluster.active_generation = 1
    cluster.active_plan_hash = "a" * 64
    if token == "no-revisions":
        committed_people_revision(client, cluster)
    cluster.panel.api_token = client.issue_token(request).token
    cluster.save()
    result = runner.invoke(app, ["client", "show", "样本", "--json"])
    assert result.exit_code == 0, result.output + str(result.exception)
    assert json.loads(result.stdout)["data"]["client"]["username"] == "sample"
    assert ClusterConfig.load().people_adoption is not None


def test_a_headerless_name_miss_cannot_erase_a_known_service_version(people_cli):
    _runner, _cluster, client = people_cli
    client.get_user("sample")
    known = client.service_version
    assert known
    assert client.get_user("样本") is None
    assert client.service_version == known


def test_recovering_a_committed_revision_forgets_only_its_adopted_bindings(people_cli):
    _runner, cluster, client = people_cli
    cluster.people_adoption = PeopleAdoption(users=["sample"])
    cluster.active_generation = 1
    cluster.active_plan_hash = "a" * 64
    for name in ["sample", "removed"]:
        cluster.managed_bindings[f"account:{name}@1"] = ManagedResourceBinding(
            logical_id=f"account:{name}", resource_kind="control_user", remote_id=name, generation=1, active=True
        )
    committed_people_revision(client, cluster)
    cluster.save()
    finish_people_adoption(cluster)
    saved = ClusterConfig.load()
    assert saved.people_adoption is None
    assert [binding.remote_id for binding in saved.managed_bindings.values()] == ["removed"]
    assert client.get_user("sample") is not None


@pytest.mark.parametrize("command", ["plan", "apply"])
@pytest.mark.parametrize("state", ["pending", "committed"])
def test_plan_and_apply_finish_a_lost_handover_before_reviewing_the_same_generation(people_cli, command, state):
    runner, cluster, client = people_cli
    cluster.people_adoption = PeopleAdoption(state=state, users=["sample"])
    plan = compile_topology(cluster.topology_intent)
    cluster.active_plan_hash, cluster.active_generation = plan.plan_hash, 1
    if state == "pending":
        committed_people_revision(client, cluster)
    cluster.save()
    runtime = MagicMock(spec=SetupRuntime)
    runtime.review.return_value = SetupReview(intent=cluster.topology_intent, plan=plan)
    runtime.apply_intent.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1)
    with (
        patch("meridian.commands.v4_topology.reviewed_runtime", return_value=runtime),
        patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        patch(
            "meridian.commands.v4_topology.inspect_profile_plan",
            return_value=ResourceInspectionResult(plan_hash=plan.plan_hash, generation=1),
        ),
        patch("meridian.commands.v4_topology.profile_review_needed", return_value=False),
    ):
        result = runner.invoke(app, [command, *(["--yes"] if command == "apply" else []), "--json"])
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "people_adoption" not in json.loads(result.stdout)["data"]
    saved = ClusterConfig.load()
    assert saved.people_adoption is None and saved.active_generation == 1
    assert client.get_user("sample") is not None


def test_loading_people_finishes_before_printing_an_obsolete_move_warning(people_cli):
    runner, cluster, _client = people_cli
    cluster.people_adoption = PeopleAdoption(state="committed", users=["sample"])
    cluster.save()
    result = runner.invoke(app, ["client", "list"])
    assert result.exit_code == 0, result.output
    assert "people move" not in result.output and "sample" in result.output


@pytest.mark.parametrize("command", ["plan", "apply"])
def test_a_pending_move_is_disclosed_once_in_the_human_topology_review(people_cli, command):
    runner, cluster, _client = people_cli
    cluster.people_adoption = PeopleAdoption(users=["sample"])
    cluster.save()
    plan = compile_topology(cluster.topology_intent)
    runtime = MagicMock(spec=SetupRuntime)
    runtime.review.return_value = SetupReview(intent=cluster.topology_intent, plan=plan)
    runtime.apply_intent.return_value = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1)
    with (
        patch("meridian.commands.v4_topology.reviewed_runtime", return_value=runtime),
        patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        patch("meridian.commands.v4_topology.profile_review_needed", return_value=False),
        patch(
            "meridian.commands.v4_topology.inspect_profile_plan",
            return_value=ResourceInspectionResult(plan_hash=plan.plan_hash, generation=1),
        ),
    ):
        result = runner.invoke(app, [command, *(["--yes"] if command == "apply" else [])])
    assert result.exit_code == (2 if command == "plan" else 0), result.output + str(result.exception)
    assert result.output.count("people move to the control service") == 1


def test_routing_confirmation_uses_the_live_display_name_and_declining_keeps_the_override(people_cli, monkeypatch):
    runner, _cluster, client = people_cli
    client.create_user(UserCreate(username="sample", display_name="样本"))
    preview = client.set_person_delivery_policy("sample", PersonDeliveryPolicyWrite(policy="full"))
    client.set_person_delivery_policy(
        "sample", PersonDeliveryPolicyWrite(policy="full", confirm=True, review_hash=preview.review_hash)
    )
    monkeypatch.setattr("meridian.console.has_tty", lambda: True)
    with patch("meridian.console.confirm", return_value=False) as confirm:
        result = runner.invoke(app, ["client", "routing", "sample", "--inherit"])
    assert result.exit_code == 1, result.output + str(result.exception)
    assert "样本" in confirm.call_args.args[0]
    assert client.get_user("sample").delivery_policy == "full"


def test_the_people_wire_keeps_beta_twelves_required_flag_as_deprecated_false(people_cli):
    class BetaTwelvePerson(BaseModel):
        username: str
        declared: bool

    _runner, _cluster, client = people_cli
    client.create_user(UserCreate(username="sample"))
    page = client.query_people(PeopleQuery())
    row = BetaTwelvePerson.model_validate(page.to_data()["users"][0])
    assert row.username == "sample" and row.declared is False
    assert type(page.users[0]).model_json_schema()["properties"]["declared"]["deprecated"] is True
