"""One kind of person: reviewed adoption, interrupted finishes, and immediate CLI edits."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from meridian.cli import app
from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler import compile_topology
from meridian.control.client import ControlClient, ControlServiceUncertain
from meridian.control.models import UserCreate, UserUpdate
from meridian.core.client_limits import UserCreationSettings
from meridian.core.people_seeds import PeopleAdoption
from meridian.core.setup import SetupDraft
from meridian.people_adoption import finish_people_adoption, forget_adopted_people, people_adoption_notice
from meridian.reconciler.meridian_drivers import ControlUserDriver
from meridian.reconciler.retirement import retire, retired_bindings
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.runtime import SetupRuntime
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.people_cli import NOW, configured
from tests.support.people_cli import people_cli as people_cli


def legacy(path, *, users=None):
    data = configured("https://198.51.100.1/secret")
    intent = data.topology_intent.to_data()
    intent["access"].update(
        users=users if users is not None else ["sample"],
        display_names={"sample": "样本"},
        creation_settings={"sample": {"traffic_limit_bytes": 123}},
    )
    if users == []:
        intent["access"] = {"users": []}
    raw = yaml.safe_dump({"version": 4, "topology_intent": intent})
    path.write_text(raw)
    return raw


@pytest.mark.parametrize("users", [["sample"], []], ids=["one-person", "nobody"])
def test_a_version_four_load_lifts_people_without_writing_then_round_trips_as_five(tmp_path, users):
    path = tmp_path / "cluster.yml"
    raw = legacy(path, users=users)
    loaded = ClusterConfig.load(path)
    assert path.read_text() == raw
    assert loaded.topology_intent.access.to_data() == {"squad_name": "Meridian Access"}
    assert loaded.version == 5
    if users:
        assert loaded.people_adoption.display_names == {"sample": "样本"}
        assert loaded.people_adoption.creation_settings["sample"].traffic_limit_bytes == 123
    else:
        assert loaded.people_adoption is None
    loaded.save(path)
    assert ClusterConfig.load(path).people_adoption == loaded.people_adoption
    assert yaml.safe_load(path.read_text())["version"] == 5


def test_adopted_accounts_are_forgotten_but_a_pending_cut_off_still_retires(tmp_path):
    with serve_control(tmp_path, clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.create_user(UserCreate(username="sample", display_name="样本"))
        client.create_user(UserCreate(username="removed"))
        cluster = configured(url)
        cluster.people_adoption = PeopleAdoption(users=["sample"])
        for name in ["sample", "removed"]:
            cluster.managed_bindings[f"account:{name}@1"] = ManagedResourceBinding(
                logical_id=f"account:{name}",
                resource_kind="control_user",
                generation=1,
                remote_id=name,
                active=True,
                cut_off_on_retire=True,
            )
        shadow = cluster.clone()
        forget_adopted_people(shadow)
        plan = compile_topology(cluster.topology_intent)
        bindings = retired_bindings(plan, shadow)
        assert [b.remote_id for b in bindings] == ["removed"]
        assert len(cluster.managed_bindings) == 2
        called = []

        class TrackingClient(ControlClient):
            def delete_user_with_cut_off(self, name):
                called.append(name)
                return super().delete_user_with_cut_off(name)

        driver = ControlUserDriver(SimpleNamespace(client=lambda: TrackingClient(url, ADMIN_TOKEN), user_cut_off={}))
        retired, errors = retire(bindings, {"control_user": driver}, shadow, lambda _state: None)
        assert not errors and retired == ["account:removed"] and called == ["removed"]
        assert client.get_user("sample") is not None and client.get_user("removed") is None


def test_adoption_is_part_of_the_review_and_review_does_not_save_forgetting(tmp_path):
    registry = ServerRegistry(tmp_path / "servers.json")
    for ref, address in [("srv-control", "198.51.100.1"), ("srv-exit", "198.51.100.2")]:
        registry.add(ServerEntry(id=ref, host=address, auth_state="validated"))
    cluster = configured("https://198.51.100.1/secret")
    cluster.people_adoption = PeopleAdoption(users=["sample"])
    cluster.managed_bindings["account:sample@1"] = ManagedResourceBinding(
        logical_id="account:sample", resource_kind="control_user", remote_id="sample", generation=1, active=True
    )
    path = tmp_path / "cluster.yml"
    cluster.save(path)
    before = path.read_bytes()
    runtime = SetupRuntime(registry, cluster_loader=lambda: cluster)
    review = runtime.review(SetupDraft.from_intent(cluster.topology_intent))
    assert review.people_adoption == ("sample",) and not review.retiring
    assert review.review_hash != review.plan.plan_hash
    assert people_adoption_notice(cluster).message == "1 people move to the control service; nothing is deleted."
    assert path.read_bytes() == before and "account:sample@1" in cluster.managed_bindings
    assert not any(r.payload.kind == "control_user" for r in review.plan.resources)


def test_a_lost_creation_reply_keeps_the_adoption_and_a_retry_keeps_the_original_keys(tmp_home):
    with serve_control(tmp_home / "service", clock=lambda: NOW) as url:
        cluster = configured(url)
        cluster.people_adoption = PeopleAdoption(
            state="committed",
            users=["sample"],
            display_names={"sample": "样本"},
            creation_settings={"sample": UserCreationSettings(traffic_limit_bytes=123)},
        )
        cluster.save()

        class LostReply(ControlClient):
            def create_user(self, create):
                super().create_user(create)
                raise ControlServiceUncertain("Lost reply", retryable=True)

        with patch("meridian.people_adoption.ControlClient", LostReply), pytest.raises(ControlServiceUncertain):
            finish_people_adoption(cluster)
        assert ClusterConfig.load().people_adoption.state == "committed"
        with ControlClient(url, ADMIN_TOKEN) as client:
            before = client.get_user("sample")
            assert before.display_name == "样本" and before.traffic_limit_bytes == 123
        resumed = ClusterConfig.load()
        finish_people_adoption(resumed)
        finish_people_adoption(resumed)
        assert ClusterConfig.load().people_adoption is None
        with ControlClient(url, ADMIN_TOKEN) as client:
            assert client.get_user("sample") == before


@pytest.mark.parametrize("origin", ["panel", "formerly-declared"])
def test_show_reissue_rotate_and_remove_find_live_service_names_for_every_person(people_cli, origin):
    runner, cluster, client = people_cli
    with client:
        person = client.create_user(UserCreate(username="sample", display_name="样本"))
        if origin == "formerly-declared":
            cluster.people_adoption = PeopleAdoption(state="committed", users=["sample"])
            cluster.save()
        client.update_user("sample", UserUpdate(display_name="Тест"))
        for command in [["show", "Тест"], ["reissue", "тест", "--yes"], ["rotate-credentials", "SAMPLE", "--yes"]]:
            result = runner.invoke(app, ["client", *command, "--json"])
            assert result.exit_code == 0, result.output + str(result.exception)
            data = json.loads(result.stdout)["data"]
            assert data["client"]["username"] == "sample"
            assert "declared" not in data["client"]
        assert client.get_user("sample").sub_token != person.sub_token
        result = runner.invoke(app, ["client", "remove", "Тест", "--yes", "--json"])
        assert result.exit_code == 0, result.output + str(result.exception)
        assert client.get_user("sample") is None


def test_batch_add_checks_every_live_alias_before_the_first_post(people_cli):
    runner, _cluster, client = people_cli
    with client:
        client.create_user(UserCreate(username="sample", display_name="样本"))
        result = runner.invoke(app, ["client", "add", "new-person", "样本", "--json"])
        assert result.exit_code == 2 and "sample" in result.output
        assert client.get_user("new-person") is None
        result = runner.invoke(app, ["client", "add", "Тест", "second", "--limit", "123MB", "--json"])
        assert result.exit_code == 0, result.output + str(result.exception)
        data = json.loads(result.stdout)["data"]
        assert len(data["clients"]) == 2 and "state_save_failed" not in result.stdout
        person = client.get_user(data["clients"][0]["username"])
        assert person.display_name == "Тест" and person.traffic_limit_bytes > 0


def test_bulk_preview_decline_and_retired_proposal_write_nothing_then_remove_the_last_people(people_cli):
    runner, _cluster, client = people_cli
    with client:
        for name in ["sample-a", "sample-b"]:
            client.create_user(UserCreate(username=name))
        result = runner.invoke(app, ["client", "remove", "sample-a", "sample-b", "--dry-run", "--json"])
        assert result.exit_code == 0, result.output + str(result.exception)
        assert "proposal" not in result.stdout and len(client.list_users()) == 2
        result = runner.invoke(app, ["client", "remove", "--proposal", "unused.json", "--json"])
        assert result.exit_code == 2 and "retired" in result.stdout
        result = runner.invoke(app, ["client", "remove", "sample-a", "sample-b", "--yes", "--json"])
        assert result.exit_code == 0, result.output + str(result.exception)
        assert not client.list_users()


def test_an_old_cli_refuses_the_new_service_before_taking_a_lease(tmp_home):
    from meridian.reconciler.fence import FleetFenceError, local_fence_view
    from meridian.service_fence import ControlServiceFence
    from tests.support.fence_host import FenceHost

    with serve_control(tmp_home / "service", version="4.0.0b13", clock=lambda: NOW) as url:
        cluster = configured(url)
        fence = ControlServiceFence(FenceHost(), cluster, cli_version="4.0.0b12")
        with pytest.raises(FleetFenceError, match="newer"):
            fence.ensure(local_fence_view(cluster))
        with ControlClient(url, ADMIN_TOKEN) as client:
            assert client.fence().record is None


def test_new_people_writes_refuse_an_old_service_with_an_apply_hint(tmp_home):
    from meridian.control_access import access_panel
    from meridian.core.errors import MeridianError

    with serve_control(tmp_home / "service", version="4.0.0b11", clock=lambda: NOW) as url:
        with access_panel(configured(url)) as panel, pytest.raises(MeridianError) as refused:
            panel.create_user(UserCreate(username="sample"))
        assert "meridian apply" in refused.value.hint
        with ControlClient(url, ADMIN_TOKEN) as client:
            assert client.get_user("sample") is None


@pytest.mark.parametrize("mode", ["declined", "json-review"])
def test_a_declined_adoption_review_saves_neither_forgetting_nor_the_lift(tmp_home, mode):
    import typer

    from meridian import config
    from meridian.commands.v4_topology import run_v4_apply
    from meridian.core.output import OperationContext

    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    for ref, address in [("srv-control", "198.51.100.1"), ("srv-exit", "198.51.100.2")]:
        registry.add(ServerEntry(id=ref, host=address, auth_state="validated"))
    raw = legacy(config.CLUSTER_CONFIG)
    cluster = ClusterConfig.load()
    cluster.managed_bindings["account:sample@1"] = ManagedResourceBinding(
        logical_id="account:sample", resource_kind="control_user", remote_id="sample", generation=1, active=True
    )
    with patch("meridian.commands.v4_topology.confirm", return_value=False), pytest.raises(typer.Exit):
        run_v4_apply(cluster, yes=False, json_output=mode == "json-review", operation=OperationContext())
    assert config.CLUSTER_CONFIG.read_text() == raw
    assert "account:sample@1" in cluster.managed_bindings


def test_pending_adoption_can_finish_after_a_crash_between_service_commit_and_local_checkpoint(tmp_home):
    from meridian.control.models import ApplyFenceRecord, RevisionProposal, RevisionResult

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        cluster = configured(url)
        cluster.people_adoption = PeopleAdoption(users=["sample"], display_names={"sample": "样本"})
        cluster.active_generation = 1
        cluster.active_plan_hash = "a" * 64
        record = client.propose_revision(
            RevisionProposal(intent=cluster.topology_intent.to_data(), plan_hash="a" * 64, review_hash="b" * 64)
        )
        client.approve_revision(record.number)
        client.record_revision_result(record.number, RevisionResult(status="applied"))
        fence = client.fence()
        client.swap_fence(
            fence.version, ApplyFenceRecord(fleet_id=FLEET_ID, generation=1, plan_hash="a" * 64, revision=record.number)
        )
        cluster.save()
        finish_people_adoption(cluster)
        assert client.get_user("sample").display_name == "样本"
        assert ClusterConfig.load().people_adoption is None


def test_service_name_lookup_exact_username_wins_then_folded_alias_ambiguity_refuses(tmp_home):
    import sqlite3

    from meridian.control_access import access_panel

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.create_user(UserCreate(username="sample-a", display_name="First"))
        client.create_user(UserCreate(username="sample-b", display_name="Second"))
        # Preserve a pre-26 collision; current service writes correctly refuse creating it.
        with sqlite3.connect(tmp_home / "service" / "control.db") as db:
            db.execute("UPDATE users SET display_name = 'SAMPLE-A' WHERE username = 'sample-b'")
        with access_panel(configured(url)) as panel:
            assert [user.username for user in panel.matching_users("sample-a")] == ["sample-a"]
            assert {user.username for user in panel.matching_users("Sample-A")} == {"sample-a", "sample-b"}


def test_test_with_zero_people_reports_unavailable_evidence(tmp_home):
    from meridian.connectivity import check_canonical_subscription
    from meridian.control_access import ServiceAccessPanel
    from meridian.verification import DeploymentVerificationContext

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url:
        run = check_canonical_subscription(
            configured(url),
            DeploymentVerificationContext(kind="v4"),
            "198.51.100.2",
            requested_client="",
            panel_factory=ServiceAccessPanel,
            xray_provider=lambda **_kw: None,
            connection_tester=lambda *_args, **_kw: None,
        )
    finding = run.checks[0].findings[0]
    assert finding.code == "ACTIVE_CLIENT_MISSING" and finding.status == "skipped"
    assert "No active" in finding.message


def test_adoption_keeps_an_existing_persons_live_name_limits_status_and_keys(tmp_home):
    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        before = client.create_user(
            UserCreate(username="sample", display_name="Current name", status="disabled", traffic_limit_bytes=17)
        )
        cluster = configured(url)
        cluster.people_adoption = PeopleAdoption(
            state="committed",
            users=["sample"],
            display_names={"sample": "Old name"},
            creation_settings={"sample": UserCreationSettings(traffic_limit_bytes=123)},
        )
        cluster.save()
        finish_people_adoption(cluster)
        assert client.get_user("sample") == before


def test_a_lost_pending_retirement_reply_never_repeats_its_atomic_cut_off(tmp_home):
    from meridian.control.client import ControlServiceUncertain

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.create_user(UserCreate(username="removed"))

        class LostReply(ControlClient):
            def delete_user_with_cut_off(self, name):
                super().delete_user_with_cut_off(name)
                raise ControlServiceUncertain("Lost cut-off reply", retryable=True)

        binding = ManagedResourceBinding(
            logical_id="account:removed",
            resource_kind="control_user",
            remote_id="removed",
            generation=1,
            active=True,
            cut_off_on_retire=True,
        )
        driver = ControlUserDriver(SimpleNamespace(client=lambda: LostReply(url, ADMIN_TOKEN), user_cut_off={}))
        with pytest.raises(ControlServiceUncertain):
            driver.retire(binding)
        driver.retire(binding)
        assert client.get_user("removed") is None


def test_name_backfill_does_not_overwrite_a_rename_between_its_read_and_write(tmp_home):
    from meridian.control.models import UserEnsure

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.create_user(UserCreate(username="sample"))

        class RenameMeanwhile(ControlClient):
            def put_user(self, username: str, *, ensure: UserEnsure | None = None):
                client.update_user(username, UserUpdate(display_name="Current name"))
                return super().put_user(username, ensure=ensure)

        cluster = configured(url)
        cluster.people_adoption = PeopleAdoption(
            state="committed", users=["sample"], display_names={"sample": "Old name"}
        )
        cluster.save()
        with patch("meridian.people_adoption.ControlClient", RenameMeanwhile):
            finish_people_adoption(cluster)
        assert client.get_user("sample").display_name == "Current name"
        assert ClusterConfig.load().people_adoption is None


def test_bulk_only_tokens_use_the_preview_version_without_needing_people_read(people_cli):
    from meridian.core.token_models import TokenCreate

    runner, cluster, client = people_cli
    with client:
        client.create_user(UserCreate(username="sample"))
        issued = client.issue_token(TokenCreate(name="Batch editor", capabilities=["people.batch", "people.private"]))
        cluster.panel.api_token = issued.token
        cluster.save()
        result = runner.invoke(app, ["client", "bulk", "disable", "--person", "sample", "--yes", "--json"])
        assert result.exit_code == 0, result.output + str(result.exception)
        assert client.get_user("sample").status == "disabled"


def test_auto_assigned_names_cannot_claim_a_later_requested_username_in_a_batch(people_cli):
    runner, _cluster, client = people_cli
    result = runner.invoke(app, ["client", "add", "Тест", " test ", "--json"])
    assert result.exit_code == 0, result.output + str(result.exception)
    people = json.loads(result.stdout)["data"]["clients"]
    assert [person["display_name"] for person in people] == ["Тест", "test"]
    assert people[0]["username"] != "test" and people[1]["username"] == "test"


def test_setup_uses_service_names_instead_of_taking_someone_elses_transliterated_username(tmp_home):
    from meridian.core.people_creation import creation_requests
    from meridian.people_adoption import ensure_creation_requests

    with serve_control(tmp_home / "service", clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as client:
        other = client.create_user(UserCreate(username="yangben", display_name="Another person"))
        known = client.create_user(UserCreate(username="yangben2", display_name="样本"))
        requests = creation_requests(["样本", "Тест"])
        first = ensure_creation_requests(client, requests)
        again = ensure_creation_requests(client, requests)
        assert first == again
        assert first[0].user_id == known.user_id
        assert first[1].display_name == "Тест"
        assert client.get_user("yangben") == other
        assert len(client.list_users()) == 3
