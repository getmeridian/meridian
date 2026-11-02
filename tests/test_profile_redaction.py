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
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings, UserCreate
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


@pytest.mark.parametrize("username", ["token", "secret", "api_key"])
def test_json_fleet_export_preserves_creation_settings_for_credential_shaped_usernames(
    tmp_path: Path, tmp_home: Path, username: str
):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username=username, profiles=["limited"]))
        intent = SetupIntent.model_validate(
            {
                **single_exit_intent(users=[username]).to_data(),
                "access_profiles": [{"id": "limited", "grants": []}],
            }
        )
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            reply = CliRunner().invoke(app, ["fleet", "export", "--json"])
        set_json_mode(False)
        assert reply.exit_code == 0, reply.output
        document = json.loads(reply.stdout)["data"]["document"]
        assert document["access"]["creation_settings"][username]["profiles"] == ["limited"]
