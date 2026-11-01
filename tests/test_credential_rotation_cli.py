"""The CLI rotates connection keys with a generation check and no credential output."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.credential_models import UserCredentialRotation
from meridian.control.models import UserRecord
from meridian.core.client_sessions import UserFlowBlocks
from tests.support.text import unstyle
from tests.support.topology import panel_mock, single_exit_intent


@pytest.fixture(autouse=True)
def clean_json_mode():
    set_json_mode(False)
    set_quiet_mode(False)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.fixture
def scene():
    before = UserRecord(
        username="alice",
        user_id="u-a",
        uuid="3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
        sub_token="test-subscription-token",
        status="active",
        created_at="2026-11-01T12:00:00Z",
    )
    after = UserRecord.model_validate(
        {**before.to_data(), "uuid": "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00", "credential_generation": 2}
    )
    result = UserCredentialRotation(user=after, flow_blocks=UserFlowBlocks(username="alice", credential_generation=2))
    panel = panel_mock(user_credentials=before, rotate_credentials=result)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/panel", api_token="test-token", server_ip="198.51.100.10"),
        topology_intent=single_exit_intent(users=["alice", "bob"]),
    )
    return cluster, panel, result


def test_cli_rotation_keeps_the_subscription_and_sends_the_read_generation(scene, tmp_home):
    cluster, panel, changed = scene
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.control_access.ServiceAccessPanel", return_value=panel),
    ):
        result = CliRunner().invoke(app, ["client", "rotate-credentials", "alice", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "client.rotate-credentials"
    assert payload["data"]["client"]["credential_generation"] == 2
    assert payload["data"]["subscription_preserved"] is True
    assert changed.user.uuid not in result.stdout and changed.user.sub_token not in result.stdout
    request = panel.rotate_credentials.call_args.args[1]
    assert request.expected_generation == 1 and not request.cut_off


def test_json_rotation_requires_yes_before_reading_credentials(scene, tmp_home):
    with patch("meridian.control_access.ServiceAccessPanel") as panel:
        result = CliRunner().invoke(app, ["client", "rotate-credentials", "alice", "--json"])
    assert result.exit_code == 2
    assert "--yes" in unstyle(result.output)
    panel.assert_not_called()


def test_cli_rotation_refuses_console_only_people_before_opening_the_service(scene, tmp_home):
    cluster, panel, _ = scene
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.control_access.ServiceAccessPanel", return_value=panel) as access,
    ):
        result = CliRunner().invoke(app, ["client", "rotate-credentials", "console-person", "--yes", "--json"])
    assert result.exit_code == 2
    access.assert_not_called()


def test_human_rotation_escapes_a_display_name_once(scene, tmp_home):
    cluster, panel, _ = scene
    cluster.topology_intent.access.display_names["alice"] = "Group [work]"
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.control_access.ServiceAccessPanel", return_value=panel),
    ):
        result = CliRunner().invoke(app, ["client", "rotate-credentials", "alice", "--yes"])
    assert result.exit_code == 0, result.output
    text = unstyle(result.output)
    assert "Group [work]" in text
    assert "\\[" not in text


def test_cli_rotation_reports_the_node_confirmation_not_the_commit_time_answer(scene, tmp_home):
    cluster, panel, changed = scene
    receipt = {
        "node_id": "main",
        "credential_generation": 1,
        "reason": "credentials",
        "required": {"fleet_epoch": 1, "revision": 4, "sha256": "a" * 64},
    }
    pending = UserFlowBlocks.model_validate(
        {"username": "alice", "credential_generation": 2, "nodes": [{**receipt, "status": "pending_node"}]}
    )
    panel.rotate_credentials.return_value = UserCredentialRotation(user=changed.user, flow_blocks=pending)
    panel.user_flow_blocks.return_value = UserFlowBlocks.model_validate(
        {"username": "alice", "credential_generation": 2, "nodes": [{**receipt, "status": "blocked"}]}
    )
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.control_access.ServiceAccessPanel", return_value=panel),
    ):
        result = CliRunner().invoke(app, ["client", "rotate-credentials", "alice", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["flow_blocks"]["nodes"][0]["status"] == "blocked"
    panel.user_flow_blocks.assert_called_with("alice")
