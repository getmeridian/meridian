"""Fleet defaults and person overrides travel through CLI, API, and typed output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client_limits import run_set
from meridian.commands.fleet_language import run_language
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


def test_cli_language_changes_are_service_owned_and_null_clears_person_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_json_mode(True)
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            client.put_user("person")
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent(users=["person"])
            )
            original = cluster.topology_intent.model_dump()
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                run_language("ru")
                state = json.loads(capsys.readouterr().out)
                schema_model("fleet-language-envelope").model_validate(state)
                assert state["data"] == {"language": "ru", "changed": True}
                run_set("person", limit=None, reset=None, expires=None, language="fa")
                person = json.loads(capsys.readouterr().out)
                schema_model("client-set-envelope").model_validate(person)
                assert person["data"]["client"]["language"] == "fa" and client.get_user("person").language == "fa"
                run_set("person", limit=None, reset=None, expires=None, language="default")
                assert json.loads(capsys.readouterr().out)["data"]["client"]["language"] is None
                assert client.get_user("person").language is None and cluster.topology_intent.model_dump() == original
    finally:
        set_json_mode(False)


def test_client_help_lists_the_optional_status_language() -> None:
    response = CliRunner().invoke(app, ["client", "set", "--help"])
    assert response.exit_code == 0 and "--language" in unstyle(response.output)
