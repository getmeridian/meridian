"""CLI notice confirmation, exact person resolution, and transient preview requests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.fleet_notice import run_notice
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


def test_notice_cli_reviews_named_person_before_confirmation_and_declining_preserves_state(tmp_path, capsys) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
        client.put_user("person")
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent(users=["person"])
        )

        def decline(*_args, **_kwargs):
            assert "Hello person" in capsys.readouterr().err
            assert client.notice().notice is None
            return False

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.fleet_notice.confirm", side_effect=decline),
            pytest.raises(typer.Exit) as raised,
        ):
            run_notice("set", text="Hello {name}", for_user="person")
        assert raised.value.exit_code == 1 and client.notice().notice is None


def test_json_notice_set_and_clear_require_yes_and_return_typed_results(tmp_path, capsys) -> None:
    set_json_mode(True)
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
            )
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                with pytest.raises(typer.Exit) as raised:
                    run_notice("set", text="Message")
                assert raised.value.exit_code == 2 and client.notice().notice is None
                schema_model("fleet-notice-set-envelope").model_validate_json(capsys.readouterr().out)
                run_notice("set", text="Message", yes=True)
                output = capsys.readouterr()
                body = json.loads(output.out)
                schema_model("fleet-notice-set-envelope").model_validate(body)
                assert body["data"]["preview"] == "Message" and body["summary"]["changed"] is True
                assert "Message" in output.err
                run_notice("clear", yes=True)
                schema_model("fleet-notice-clear-envelope").model_validate_json(capsys.readouterr().out)
                assert client.notice().notice is None
    finally:
        set_json_mode(False)


def test_notice_help_exposes_preview_and_confirm_flags() -> None:
    reply = CliRunner().invoke(app, ["fleet", "notice", "set", "--help"])
    text = unstyle(reply.output)
    assert reply.exit_code == 0
    assert all(flag in text for flag in ("--until", "--for", "--yes", "--json"))
