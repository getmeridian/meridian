"""Membership evidence survives a missing map, and every reviewed map must include it."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import config
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings, UserCreate, UserUpdate
from meridian.control.profile_declarations import DECLARATIONS_META
from meridian.core.access_profile_views import AccessProfileReviewRequest
from meridian.core.access_profiles import AccessProfileIntent, profile_declarations_hash
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.fleet_document import dump_fleet_document, fleet_document
from meridian.setup.profile_review import profile_review_needed
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent
from tests.test_access_profiles import REALITY, configure_profiles
from tests.test_access_profiles import service as service


@pytest.fixture(autouse=True)
def command_modes():
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def lose_profile_map(database: Path):
    with sqlite3.connect(database) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('access_profiles', '{}')")
        db.execute("DELETE FROM meta WHERE key = ?", (DECLARATIONS_META,))


def test_non_default_memberships_require_review_even_without_a_live_map_or_fingerprint(tmp_path: Path):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username="person", profiles=["limited"]))
        lose_profile_map(tmp_path / "control.db")
        live = admin.access_profiles()
        assert live.declarations_hash is None
        assert all(row.built_in for row in live.profiles)
        assert live.has_non_default_memberships is True
        intent = single_exit_intent()
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        assert profile_review_needed(cluster, intent) is True


@pytest.mark.parametrize("profiles", [{}, {"other": []}], ids=["empty-map", "other-profile"])
@pytest.mark.parametrize("entrypoint", ["preview", "reviewed-write"])
def test_reviewed_maps_refuse_to_leave_people_on_profiles_absent_from_the_live_map(
    service, tmp_path: Path, profiles: dict[str, list[str]], entrypoint: str
):
    configure_profiles(service)
    person = service.create_user(UserCreate(username="person", profiles=["limited"]))
    lose_profile_map(tmp_path / "control.db")
    before = service.settings()
    access_hash = service.access.catalog().access_hash
    with pytest.raises(ServiceError, match="limited is used by 1 person") as refused:
        if entrypoint == "preview":
            service.access.preview(AccessProfileReviewRequest(access_profiles=profiles))
        else:
            declarations = profile_declarations_hash(
                [AccessProfileIntent(id=name, grants=refs) for name, refs in profiles.items()]
            )
            service.put_settings(
                ServiceSettings.model_validate(
                    {**before.to_data(), "access_profiles": profiles, "access_profile_declarations_hash": declarations}
                ),
                expected_access_hash=access_hash,
            )
    assert refused.value.code == "profile_in_use"
    assert service.settings() == before
    assert service.access.catalog().access_hash == access_hash
    assert service.access.catalog().declarations_hash is None
    assert service.get_user("person") == person


@pytest.mark.parametrize("repair", ["restore-profile", "set-membership"])
def test_a_missing_profile_map_can_be_repaired_without_changing_credentials(service, tmp_path: Path, repair: str):
    configure_profiles(service)
    person = service.create_user(UserCreate(username="person", profiles=["limited"]))
    lose_profile_map(tmp_path / "control.db")
    profiles = {"limited": [REALITY]}
    if repair == "set-membership":
        service.update_user("person", UserUpdate(profiles=["default"]))
        profiles = {"other": []}
    review = service.access.preview(AccessProfileReviewRequest(access_profiles=profiles))
    service.put_settings(
        ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": profiles}),
        expected_access_hash=review.access_hash,
    )
    after = service.get_user("person")
    assert after.profiles == (["limited"] if repair == "restore-profile" else ["default"])
    assert (after.uuid, after.sub_token, after.credential_generation) == (
        person.uuid,
        person.sub_token,
        person.credential_generation,
    )
    assert service.settings().access_profiles == profiles


@pytest.mark.parametrize("command", ["apply", "fleet-import", "profile-add"])
def test_cli_review_refuses_missing_profile_memberships_before_applying(tmp_path: Path, tmp_home: Path, command: str):
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    intent = single_exit_intent()
    wanted = intent
    module = "meridian.commands.v4_topology"
    argv = ["apply", "--yes", "--json"]
    if command == "fleet-import":
        wanted = type(intent).model_validate({**intent.to_data(), "delivery": {"client_routing": "full"}})
        source = tmp_path / "fleet.yml"
        source.write_text(dump_fleet_document(fleet_document(wanted, registry)))
        module = "meridian.commands.fleet_transfer"
        argv = ["fleet", "import", str(source), "--yes", "--json"]
    elif command == "profile-add":
        wanted = type(intent).model_validate({**intent.to_data(), "access_profiles": [{"id": "other", "grants": []}]})
        module = "meridian.commands.profiles"
        argv = ["profile", "add", "other", "--empty", "--yes", "--json"]
    plan = compile_topology(wanted)
    result = ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        person = admin.create_user(UserCreate(username="person", profiles=["limited"]))
        lose_profile_map(tmp_path / "control.db")
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch(module + ".reviewed_runtime") as runtime,
            patch("meridian.commands.v4_topology.forget_unreachable_servers"),
            patch("meridian.commands.fleet_transfer.forget_unreachable_servers"),
            patch("meridian.commands.fleet_transfer.apply_reviewed_intent", return_value=result) as import_apply,
            patch("meridian.commands.profiles.apply_reviewed_intent", return_value=result) as profile_apply,
        ):
            runtime.return_value.review.return_value = SimpleNamespace(plan=plan, review_hash="a" * 64, retiring=[])
            runtime.return_value.apply_intent.return_value = result
            reply = CliRunner().invoke(app, ["--lang", "en", *argv])
        assert reply.exit_code == 2, reply.output
        message = json.loads(reply.stdout)["errors"][0]["message"]
        assert "limited" in message and "used by 1 person" in message
        runtime.return_value.apply_intent.assert_not_called()
        import_apply.assert_not_called()
        profile_apply.assert_not_called()
        assert admin.settings().access_profiles == {}
        assert admin.access_profiles().declarations_hash is None
        assert admin.get_user("person") == person


@pytest.mark.parametrize("health", ["answers", "unreachable"])
@pytest.mark.parametrize("used", [False, True])
def test_a_service_older_than_profiles_needs_no_review_only_while_it_answers_health(
    tmp_path: Path, health: str, used: bool
):
    """Plan before an upgrade meets a service without profile routes; only a live 404 proves it holds none."""
    from meridian.control.client import ControlServiceError, ControlServiceNotFound
    from meridian.setup.profile_review import ProfileReviewUnavailableError

    def no_route(_client):
        raise ControlServiceNotFound("Not found")

    def down(_client):
        raise ControlServiceError("The control service did not answer", category="system")

    intent = single_exit_intent()
    with serve_control(tmp_path) as url:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent, access_profiles_used=used
        )
        with (
            patch.object(ControlClient, "access_profiles", no_route),
            patch.object(ControlClient, "health", ControlClient.health if health == "answers" else down),
        ):
            if health == "answers" and not used:
                assert profile_review_needed(cluster, intent) is False
            else:
                with pytest.raises(ProfileReviewUnavailableError):
                    profile_review_needed(cluster, intent)
