"""The CLI reviews private delivery choices, including people created in the console."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent
from tests.test_delivery_policies import delivery_data


def test_person_routing_help_exposes_review_and_inheritance() -> None:
    response = CliRunner().invoke(app, ["client", "routing", "--help"])
    text = unstyle(response.output)
    assert response.exit_code == 0, text
    assert all(flag in text for flag in ("--dry-run", "--yes", "--json", "default"))


def test_person_routing_dry_run_and_confirm_use_the_service_review(tmp_path: Path) -> None:
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            client.put_user("guest")
            client.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
            )
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                result = CliRunner().invoke(app, ["client", "routing", "guest", "full", "--dry-run", "--json"])
                assert result.exit_code == 0, result.output
                body = json.loads(result.stdout)
                assert body["command"] == "client.routing" and body["data"]["dry_run"]
                assert body["data"]["state"]["effective"]["policy"]["id"] == "full"
                assert client.get_user("guest").delivery_policy is None
                result = CliRunner().invoke(app, ["client", "routing", "guest", "full", "--yes", "--json"])
                assert result.exit_code == 0, result.output
                assert json.loads(result.stdout)["data"]["applied"]
                assert client.get_user("guest").delivery_policy == "full"
                result = CliRunner().invoke(app, ["client", "routing", "guest", "--inherit", "--yes", "--json"])
                assert result.exit_code == 0, result.output
                assert client.get_user("guest").delivery_policy is None
    finally:
        set_json_mode(False)


def test_default_is_a_valid_policy_name_not_a_clear_request(tmp_path: Path) -> None:
    from tests.test_delivery_policies import policy_data

    data = policy_data()
    data["id"] = "default"
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            client.put_user("guest")
            client.put_settings(
                ServiceSettings.model_validate({"profile_title": "Meridian", "delivery_policies": [data]})
            )
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
            )
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                result = CliRunner().invoke(app, ["client", "routing", "guest", "default", "--yes", "--json"])
            assert result.exit_code == 0, result.output
            assert client.get_user("guest").delivery_policy == "default"
    finally:
        set_json_mode(False)
