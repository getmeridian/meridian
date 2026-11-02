"""The matcher command reads applied service rules and never edits fleet intent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.models import ServiceSettings
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN
from tests.support.text import unstyle
from tests.support.token_service import serve_tokens
from tests.support.topology import single_exit_intent
from tests.test_response_rules import app_rule


def test_response_rule_command_reads_applied_rules_in_a_typed_json_envelope(tmp_home: Path) -> None:
    with serve_tokens(tmp_home) as fleet:
        fleet.service.put_settings(
            ServiceSettings.model_validate(
                {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "format", "format": "singbox"})]}
            )
        )
        cluster = ClusterConfig(
            topology_intent=single_exit_intent(), panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN)
        )
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            result = CliRunner().invoke(app, ["fleet", "response-rules", "test", "--user-agent", "Happ/3", "--json"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout)
        schema_model("fleet-response-rules-test-envelope").model_validate_json(result.stdout)
        assert envelope["command"] == "fleet.response-rules.test"
        assert envelope["data"]["selection"]["name"] == "happ"
        assert envelope["data"]["format"] == "singbox"
        assert "Happ/3" not in result.stdout


def test_response_rule_help_lists_the_bounded_transient_request_options() -> None:
    result = CliRunner().invoke(app, ["fleet", "response-rules", "test", "--help"])
    assert result.exit_code == 0, result.output
    text = unstyle(result.output)
    for option in ("--user-agent", "--accept", "--person", "--suffix", "--json"):
        assert option in text
