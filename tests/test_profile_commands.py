"""Profiles use reviewed topology; membership edits use service reviews and keep links."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.core.topology import SetupIntent
from meridian.reconciler.resources import ResourceExecutionResult
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def modes(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def test_profile_commands_and_bulk_assignment_are_public_with_review_flags():
    runner = CliRunner()
    for command in ("add", "set", "remove", "list"):
        result = runner.invoke(app, ["profile", command, "--help"])
        assert result.exit_code == 0, result.output
        if command != "list":
            assert "--yes" in unstyle(result.output) and "--dry-run" in unstyle(result.output)
    bulk = runner.invoke(app, ["client", "bulk", "set-profiles", "--help"])
    assert bulk.exit_code == 0, bulk.output
    assert "--profile" in unstyle(bulk.output) and "--search" in unstyle(bulk.output)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_client_profile_review_and_bulk_commit_preserve_other_choices_and_credentials(
    tmp_path: Path, tmp_home: Path, locale: str
):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        original = admin.put_user("service-person")
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent())
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            runner = CliRunner()
            preview = runner.invoke(
                app,
                ["--lang", locale, "client", "set", "service-person", "--profile", "limited", "--dry-run", "--json"],
            )
            assert preview.exit_code == 0, preview.output
            assert admin.get_user("service-person").profiles == ["default"]
            refused = runner.invoke(app, ["client", "set", "service-person", "--profile", "limited", "--json"])
            assert refused.exit_code == 2
            changed = runner.invoke(
                app, ["--lang", locale, "client", "set", "service-person", "--profile", "limited", "--yes", "--json"]
            )
            assert changed.exit_code == 0, changed.output
            assert json.loads(changed.stdout)["data"]["client"]["profiles"] == ["limited"]
            shown = runner.invoke(app, ["client", "show", "service-person", "--json"])
            assert shown.exit_code == 0, shown.output
            assert json.loads(shown.stdout)["data"]["access"]["profiles"] == ["limited"]
            bulk = runner.invoke(
                app,
                [
                    "client",
                    "bulk",
                    "set-profiles",
                    "--person",
                    "service-person",
                    "--profile",
                    "default",
                    "--yes",
                    "--json",
                ],
            )
            assert bulk.exit_code == 0, bulk.output
        current = admin.get_user("service-person")
        assert current.profiles == ["default"]
        assert (current.uuid, current.sub_token, current.credential_generation, current.device_limit) == (
            original.uuid,
            original.sub_token,
            original.credential_generation,
            original.device_limit,
        )


def test_profile_add_reviews_the_compiled_grants_and_fences_membership_at_apply(tmp_path: Path, tmp_home: Path):
    with serve_control(tmp_path) as url:
        intent = single_exit_intent()
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        proposed = SetupIntent.model_validate(
            {**intent.to_data(), "access_profiles": [{"id": "limited", "grants": ["exit:exit-a"]}]}
        )
        review = SimpleNamespace(plan=compile_topology(proposed), review_hash="a" * 64, retiring=[])
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.profiles.reviewed_runtime") as runtime,
            patch(
                "meridian.commands.profiles.apply_reviewed_intent",
                return_value=ResourceExecutionResult(plan_hash="a" * 64, generation=2),
            ) as apply,
        ):
            runtime.return_value.review.return_value = review
            runner = CliRunner()
            dry = runner.invoke(app, ["profile", "add", "limited", "--grant", "exit:exit-a", "--dry-run", "--json"])
            assert dry.exit_code == 0, dry.output
            apply.assert_not_called()
            result = runner.invoke(app, ["profile", "add", "limited", "--grant", "exit:exit-a", "--yes", "--json"])
            assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)["data"]
        assert body["access_review"]["access_profiles"] == {"limited": ["inbound:exit-a:reality-a"]}
        assert apply.call_args.kwargs["expected_plan_hash"] == review.review_hash
        assert apply.call_args.kwargs["access_review_hash"] == body["access_review"]["access_hash"]


def test_default_profile_removal_and_invalid_membership_are_refused_before_service_access():
    with patch("meridian.commands._helpers.ClusterConfig.load") as load:
        for argv in (
            ["profile", "remove", "default", "--yes", "--json"],
            ["client", "set", "person", "--profile", "bad name", "--yes", "--json"],
        ):
            result = CliRunner().invoke(app, argv)
            assert result.exit_code == 2, result.output
        load.assert_not_called()
