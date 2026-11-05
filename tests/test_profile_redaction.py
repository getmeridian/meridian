"""Explicit metadata reveals preserve containers with credential-shaped identifiers."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.profile_review import profile_review_paths
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.access_profile_views import AccessProfileReview
from meridian.core.client_sessions import CutOffPlan
from meridian.core.redaction import REDACTED, redact
from meridian.core.topology import SetupIntent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def command_modes():
    yield
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.mark.parametrize("profile", ["token", "secret", "api_key"])
def test_profile_review_reveals_list_grants_for_credential_shaped_profile_ids(profile: str):
    review = AccessProfileReview(
        access_hash="a" * 64,
        access_profiles={profile: ["inbound:exit-a:reality-a"]},
        people=[],
        gained_people=0,
        lost_people=0,
        cut_off=CutOffPlan(nodes=[]),
    )
    result = redact({"data": {"access_review": review}}, reveal=profile_review_paths(review))
    assert result["data"]["access_review"]["access_profiles"][profile] == ["inbound:exit-a:reality-a"]


def test_revealed_containers_keep_nested_metadata_and_redact_real_string_credentials():
    payload = {
        "metadata": {
            "token": [{"secret": {"count": 2, "profiles": ["secret"], "api_token": "sample-private-token"}}],
            "api_key": {"secret": True, "private_key": "sample-private-key"},
            "url": "https://vpn.example.org/sub/sample-private-page",
        },
        "other": {"token": {"count": 3}},
    }
    result = redact(payload, reveal=frozenset({"metadata"}))
    assert result["metadata"]["token"][0]["secret"] == {
        "count": 2,
        "profiles": ["secret"],
        "api_token": REDACTED,
    }
    assert result["metadata"]["api_key"] == {"secret": True, "private_key": REDACTED}
    assert result["metadata"]["url"] == "https://vpn.example.org/" + REDACTED
    assert result["other"]["token"] == REDACTED


def mapped_profile_intent(profile: str) -> SetupIntent:
    data = single_exit_intent().to_data()
    data["access_profiles"] = [{"id": profile, "grants": []}]
    data["delivery"] = {**data.get("delivery", {}), "profile_policies": {profile: "full"}}
    return SetupIntent.model_validate(data)


@pytest.mark.parametrize("profile", ["token", "secret", "api_key"])
def test_revisions_accept_credential_shaped_profile_ids_as_delivery_default_keys(profile: str):
    from meridian.control.revisions import secret_paths

    intent = mapped_profile_intent(profile).model_dump(mode="json", by_alias=True)
    assert secret_paths(intent) == []
    intent["delivery"]["profile_policies"]["password"] = "full"
    assert secret_paths(intent) == ["delivery.profile_policies.password"]
    intent["delivery"]["profile_policies"] = {profile: {"api_token": "full"}}
    assert secret_paths(intent) == [f"delivery.profile_policies.{profile}"]


@pytest.mark.parametrize("profile", ["token", "secret", "api_key"])
def test_json_fleet_export_preserves_delivery_defaults_for_credential_shaped_profile_ids(
    tmp_path: Path, tmp_home: Path, profile: str
):
    with serve_control(tmp_path) as url:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=mapped_profile_intent(profile)
        )
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            reply = CliRunner().invoke(app, ["fleet", "export", "--json"])
        set_json_mode(False)
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.stdout)["data"]["document"]["delivery"]["profile_policies"] == {profile: "full"}
