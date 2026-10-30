"""CLI format preview uses bounded inputs and returns no subscription credentials."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client_preview import run_preview
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


def test_cli_preview_returns_typed_redacted_output_for_console_people(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_json_mode(True)
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            user = client.put_user("guest")
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
            )
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                run_preview(" guest ", user_agent="Hiddify/2", accept="application/json")
            output = capsys.readouterr().out
            body = json.loads(output)
            schema_model("client-preview-envelope").model_validate(body)
            assert body["data"]["format"] == "singbox" and body["data"]["selection"]["rule"] == "user_agent"
            assert user.uuid not in output and user.sub_token not in output
    finally:
        set_json_mode(False)


@pytest.mark.parametrize(
    "inputs",
    [
        {"user_agent": "x" * 513},
        {"user_agent": "a\nb"},
        {"user_agent": "Happ/1", "accept": "x" * 257},
        {"user_agent": "Happ/1", "suffix": "unknown"},
    ],
)
def test_cli_preview_validates_header_inputs_before_any_connection(inputs: dict) -> None:
    with (
        patch("meridian.commands.client_preview.access_panel", side_effect=AssertionError("must not connect")),
        pytest.raises(typer.Exit) as raised,
    ):
        run_preview("person", **inputs)
    assert raised.value.exit_code == 2


def test_preview_help_exposes_transient_headers_and_explicit_suffix() -> None:
    response = CliRunner().invoke(app, ["client", "preview", "--help"])
    text = unstyle(response.output)
    assert response.exit_code == 0 and all(flag in text for flag in ("--user-agent", "--accept", "--suffix", "--json"))
