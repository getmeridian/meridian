"""Profile reviews refuse the same delivery conflicts as writes and initial compilation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.errors import TopologyCompileError
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings, UserCreate
from meridian.control.service import ControlService
from meridian.core.people_bulk import BulkCommitRequest, BulkPreviewRequest
from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
from meridian.core.topology import DeliveryIntent, SetupIntent
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import Fleet, serve_tokens, token_request
from tests.support.topology import single_exit_intent
from tests.test_access_profiles import NOW, POLICY, spec


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fleet]:
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    monkeypatch.setattr("meridian.commands.client_bulk.utc_now", lambda: NOW)
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    with serve_tokens(tmp_path) as fleet:
        fleet.clock.now = datetime.fromisoformat(NOW).timestamp()
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        yield fleet
    set_json_mode(False)
    set_quiet_mode(False)


def select_defaults(service: ControlService, *, conflict: bool = True) -> None:
    service.put_settings(
        ServiceSettings.model_validate(
            {
                **service.settings().to_data(),
                "profile_policies": {"limited": "full", "udp": "ru-split" if conflict else "full"},
            }
        )
    )


def person_override(service: ControlService, username: str) -> None:
    review = service.delivery.set_person(username, PersonDeliveryPolicyWrite(policy="full"))
    service.delivery.set_person(
        username, PersonDeliveryPolicyWrite(policy="full", confirm=True, review_hash=review.review_hash)
    )


def profile_request(names: list[str]) -> BulkPreviewRequest:
    return BulkPreviewRequest.model_validate(
        {"usernames": names, "action": {"action": "set-profiles", "profiles": ["limited", "udp"]}}
    )


def undo_receipt(service: ControlService, *, override: bool = False) -> str:
    service.create_user(UserCreate(username="person", profiles=["limited", "udp"]))
    if override:
        person_override(service, "person")
    preview = service.bulk.preview(
        BulkPreviewRequest.model_validate(
            {"usernames": ["person"], "action": {"action": "set-profiles", "profiles": ["limited"]}}
        )
    )
    receipt = service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
    select_defaults(service)
    return receipt.id


@pytest.mark.parametrize("names", [["person"], ["other", "person"]], ids=["single", "bulk"])
@pytest.mark.parametrize("query", [False, True])
def test_profile_previews_resolve_each_person_before_returning_a_review(
    fleet: Fleet, names: list[str], query: bool
) -> None:
    for name in ("person", "other"):
        fleet.service.put_user(name)
    person_override(fleet.service, "other")
    select_defaults(fleet.service)
    request = profile_request(names)
    if query:
        request = BulkPreviewRequest.model_validate({"query": {}, "action": request.action.to_data()})
    writes, events = fleet.service._db.total_changes, fleet.service.events.list()
    with pytest.raises(ServiceError) as caught:
        fleet.service.bulk.preview(request)
    assert (caught.value.code, caught.value.status) == ("conflicting_delivery_policies", 409)
    assert fleet.service._db.total_changes == writes
    assert fleet.service.events.list() == events
    assert all(fleet.service.get_user(name).profiles == ["default"] for name in names)


def test_profile_preview_api_returns_the_write_conflict(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    select_defaults(fleet.service)
    writes = fleet.service._db.total_changes
    status, result = token_request(
        fleet.url + "/api/people/bulk/preview", "POST", profile_request(["person"]).to_data(), ADMIN_TOKEN
    )
    assert status == 409 and result["error"]["code"] == "conflicting_delivery_policies"
    assert "different delivery policies" in result["error"]["message"]
    assert fleet.service._db.total_changes == writes


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_profile_dry_runs_refuse_the_write_conflict(fleet: Fleet, tmp_home: Path, bulk: bool, locale: str) -> None:
    fleet.service.put_user("person")
    select_defaults(fleet.service)
    cluster = ClusterConfig(
        panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
    )
    argv = ["client", "bulk", "set-profiles", "--person", "person"] if bulk else ["client", "set", "person"]
    argv += ["--profile", "limited", "--profile", "udp", "--dry-run", "--json"]
    writes = fleet.service._db.total_changes
    with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
        result = CliRunner().invoke(app, ["--lang", locale, *argv])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["errors"][0]["message"] == i18n.t("delivery.error_conflicting_delivery_policies")
    assert fleet.service._db.total_changes == writes


def test_person_override_allows_conflicting_profile_defaults_in_preview_commit_and_undo(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    person_override(fleet.service, "person")
    select_defaults(fleet.service)
    writes = fleet.service._db.total_changes
    preview = fleet.service.bulk.preview(profile_request(["person"]))
    assert preview.people[0].after is not None and preview.people[0].after.profiles == ["limited", "udp"]
    assert fleet.service._db.total_changes == writes
    receipt = fleet.service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
    assert fleet.service.delivery.person("person").effective.policy.id == "full"
    inverse = fleet.service.bulk.undo(receipt.id, dry_run=True)
    assert inverse.undo_people is not None and inverse.undo_people[0].after.profiles == ["default"]
    fleet.service.bulk.undo(receipt.id)
    assert fleet.service.get_user("person").delivery_policy == "full"
    assert fleet.service.get_user("person").profiles == ["default"]


def test_commit_rechecks_defaults_changed_after_a_valid_preview(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    select_defaults(fleet.service, conflict=False)
    preview = fleet.service.bulk.preview(profile_request(["person"]))
    select_defaults(fleet.service)
    writes = fleet.service._db.total_changes
    with pytest.raises(ServiceError) as caught:
        fleet.service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
    assert caught.value.code == "conflicting_delivery_policies"
    assert fleet.service.get_user("person").profiles == ["default"]
    assert fleet.service._db.total_changes == writes


@pytest.mark.parametrize("dry_run", [False, True])
def test_undo_resolves_restored_profiles_before_a_dry_run_or_write(fleet: Fleet, dry_run: bool) -> None:
    receipt = undo_receipt(fleet.service)
    writes, events = fleet.service._db.total_changes, fleet.service.events.list()
    with pytest.raises(ServiceError) as caught:
        fleet.service.bulk.undo(receipt, dry_run=dry_run)
    assert (caught.value.code, caught.value.status) == ("conflicting_delivery_policies", 409)
    assert fleet.service._db.total_changes == writes
    assert fleet.service.events.list() == events
    assert fleet.service.bulk.receipt(receipt).undone_at is None
    assert fleet.service.get_user("person").profiles == ["limited"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_undo_dry_run_refuses_conflicting_restored_profiles(fleet: Fleet, tmp_home: Path, locale: str) -> None:
    receipt = undo_receipt(fleet.service)
    cluster = ClusterConfig(
        panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
    )
    writes = fleet.service._db.total_changes
    with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
        result = CliRunner().invoke(app, ["--lang", locale, "client", "bulk", "undo", receipt, "--dry-run", "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["errors"][0]["message"] == i18n.t("delivery.error_conflicting_delivery_policies")
    assert fleet.service._db.total_changes == writes
    assert fleet.service.bulk.receipt(receipt).undone_at is None


def test_undo_keeps_a_person_override_when_restoring_conflicting_profiles(fleet: Fleet) -> None:
    receipt = undo_receipt(fleet.service, override=True)
    preview = fleet.service.bulk.undo(receipt, dry_run=True)
    assert preview.undo_people is not None and preview.undo_people[0].after.profiles == ["limited", "udp"]
    fleet.service.bulk.undo(receipt)
    person = fleet.service.get_user("person")
    assert person.profiles == ["limited", "udp"] and person.delivery_policy == "full"


@pytest.mark.parametrize("boundary", ["intent", "compile"])
def test_initial_profiles_refuse_conflicting_delivery_defaults_at_both_boundaries(boundary: str) -> None:
    data = single_exit_intent().to_data()
    data["access_profiles"] = [{"id": name, "grants": []} for name in ("limited", "udp")]
    data["access"]["creation_settings"] = {"default": {"profiles": ["limited", "udp"]}}
    if boundary == "intent":
        data["delivery"]["profile_policies"] = {"limited": "full", "udp": "ru-split"}
        with pytest.raises(ValidationError, match="default.*different delivery policies"):
            SetupIntent.model_validate(data)
    else:
        original = SetupIntent.model_validate(data)
        proposed = original.model_copy(
            update={"delivery": DeliveryIntent(profile_policies={"limited": "full", "udp": "ru-split"})}
        )
        with pytest.raises(TopologyCompileError, match="default.*different delivery policies"):
            compile_topology(proposed)


@pytest.mark.parametrize(
    "profiles, defaults",
    [
        (["limited", "udp"], {"limited": "full", "udp": "full"}),
        (["limited"], {"limited": "full", "udp": "ru-split"}),
        (["limited", "udp"], {"limited": "full"}),
        (["default"], {"limited": "full", "udp": "ru-split"}),
    ],
)
def test_initial_profiles_allow_agreeing_or_unmapped_defaults(profiles: list[str], defaults: dict[str, str]) -> None:
    data = single_exit_intent().to_data()
    data["access_profiles"] = [{"id": name, "grants": []} for name in ("limited", "udp")]
    data["access"]["creation_settings"] = {"default": {"profiles": profiles}}
    data["delivery"]["profile_policies"] = defaults
    plan = compile_topology(SetupIntent.model_validate(data))
    assert plan.resources
