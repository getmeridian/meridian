"""Bulk CLI reviews use the service query and require explicit JSON confirmation."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import UserUpdate
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def isolated_cli_state(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)


def test_bulk_actions_are_public_with_shared_selection_flags():
    runner = CliRunner()
    for action in (
        "disable",
        "extend",
        "quota",
        "expiry",
        "reset-traffic",
        "enable",
        "tag",
        "devices",
        "renewal",
        "delete",
    ):
        result = runner.invoke(app, ["client", "bulk", action, "--help"])
        assert result.exit_code == 0
        assert "--search" in unstyle(result.output) and "--attention-reason" in unstyle(result.output)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_bulk_json_preview_and_commit_match_the_list_query(tmp_path, tmp_home, locale):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_user("sample-a")
        admin.put_user("sample-b")
        admin.update_user("sample-a", UserUpdate(tag="GROUP_A"))
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(users=["topology-person"]),
        )
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            runner = CliRunner()
            listed = runner.invoke(
                app, ["--lang", locale, "client", "list", "--tag", "GROUP_A", "--status", "active", "--json"]
            )
            assert listed.exit_code == 0, listed.output
            expected = [row["username"] for row in json.loads(listed.stdout)["data"]["clients"]]
            reviewed = runner.invoke(
                app,
                [
                    "--lang",
                    locale,
                    "client",
                    "bulk",
                    "disable",
                    "--tag",
                    "GROUP_A",
                    "--status",
                    "active",
                    "--dry-run",
                    "--json",
                ],
            )
            assert reviewed.exit_code == 0, reviewed.output
            review = json.loads(reviewed.stdout)["data"]["preview"]
            assert [row["username"] for row in review["people"]] == expected == ["sample-a"]
            refused = runner.invoke(app, ["client", "bulk", "disable", "--tag", "GROUP_A", "--json"])
            assert refused.exit_code == 2 and admin.get_user("sample-a").status == "active"
            applied = runner.invoke(app, ["client", "bulk", "disable", "--tag", "GROUP_A", "--yes", "--json"])
            assert applied.exit_code == 0, applied.output
            receipt = json.loads(applied.stdout)["data"]["receipt"]
            assert receipt["counts"]["changed"] == 1
            assert admin.get_user("sample-b").status == "active"
            undone = runner.invoke(app, ["client", "bulk", "undo", receipt["id"], "--yes", "--json"])
            assert undone.exit_code == 0, undone.output
            assert admin.get_user("sample-a").status == "active"


def test_invalid_bulk_action_parameters_are_refused_before_opening_the_service():
    with patch("meridian.commands._helpers.ClusterConfig.load") as load:
        result = CliRunner().invoke(app, ["client", "bulk", "extend", "--days", "0", "--yes", "--json"])
    assert result.exit_code == 2
    load.assert_not_called()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_client_list_json_keeps_secret_shaped_tag_counts_as_integers(tmp_path, tmp_home, locale):
    from meridian.core.schema import schema_model

    tags = ["TOKEN", "SECRET", "PASSWORD", "PASSWD", "JWT", "URI", "API_KEY", "VIP_TOKEN"]
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        for index, tag in enumerate(tags):
            username = f"sample-tag-{index}"
            admin.put_user(username)
            admin.update_user(username, UserUpdate(tag=tag))
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(users=["topology-person"]),
        )
        with patch("meridian.commands.client.load_topology_cluster", return_value=cluster):
            result = CliRunner().invoke(app, ["--lang", locale, "client", "list", "--limit", "2", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert all(payload["data"]["facets"]["tag"][tag] == 1 for tag in tags)
        schema_model("client-list-envelope").model_validate(payload)
