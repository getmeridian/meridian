"""Public command flags and typed outcomes for node restarts and urgent cut-offs."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_reissue, run_remove
from meridian.console import set_json_mode
from meridian.control.command_models import CutOffNode, CutOffPlan, NodeCommandOutcome
from meridian.handoff import HandoffLinks
from meridian.remnawave import User
from tests.support.node_commands import command_record
from tests.support.topology import single_exit_intent


@pytest.mark.parametrize("command", [["client", "remove"], ["client", "reissue"]])
def test_client_help_discloses_the_opt_in_cut_off_scope(command: list[str]) -> None:
    reply = CliRunner().invoke(app, [*command, "--help"])
    assert reply.exit_code == 0
    assert "--cut-off" in reply.output
    assert "every" in reply.output.lower() and "session" in reply.output.lower()
    assert "restart" in reply.output.lower()


def cluster() -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.10/console",
            api_token="test-admin-token",
            server_ip="198.51.100.10",
            sub_path="pages",
        ),
        topology_intent=single_exit_intent(users=["person-a", "person-b"]),
    )


def person(*, reissued: bool = False) -> User:
    return User(
        username="person-a",
        id="u-a",
        short_uuid="new-subscription-id" if reissued else "old-subscription-id",
        vless_uuid="00000000-0000-4000-8000-000000000002" if reissued else "00000000-0000-4000-8000-000000000001",
        status="ACTIVE",
    )


def test_client_remove_json_preserves_all_cut_off_results_after_the_reviewed_apply(tmp_home, capsys) -> None:
    saved = cluster()
    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.get_user.return_value = person()
    outcomes = [
        NodeCommandOutcome(
            node_id="main", status="core_restart", commands=[command_record("main", "succeeded", fallback=True)]
        )
    ]
    scope = CutOffPlan(nodes=[CutOffNode(node_id="main", inbound_tags=["vless-reality"])])
    events = []
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.client.load_topology_cluster", return_value=saved),
            patch("meridian.commands.client_v4.access_panel", return_value=panel),
            patch("meridian.node_commands.cut_off_scope", return_value=scope),
            patch(
                "meridian.commands.client_v4.apply_reviewed_intent",
                side_effect=lambda *args, **kwargs: events.append("apply"),
            ),
            patch(
                "meridian.node_commands.cut_off_after_apply",
                side_effect=lambda *args: events.append("cut-off") or outcomes,
            ),
            patch("meridian.handoff.remove_connection_page", return_value=""),
            patch("meridian.commands.client_v4.persist_reviewed_apply"),
        ):
            run_remove("person-a", yes=True, cut_off=True)
        result = json.loads(capsys.readouterr().out)
        assert result["data"]["cut_off"][0]["status"] == "core_restart"
        assert events == ["apply", "cut-off"]
    finally:
        set_json_mode(False)


def test_reissue_queues_the_cut_off_before_publishing_and_keeps_the_new_links(tmp_home, capsys) -> None:
    saved = cluster()
    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.get_user.return_value = person()
    record = command_record("main", "succeeded")
    panel.revoke_user_subscription_with_cut_off.return_value = (person(reissued=True), [record])
    links = HandoffLinks(
        "person-a",
        page_url="https://198.51.100.10/pages/new-page",
        subscription_url="https://198.51.100.10/pages/sub/new-subscription-id",
    )
    scope = CutOffPlan(nodes=[CutOffNode(node_id="main", inbound_tags=["vless-reality"])])
    outcomes = [NodeCommandOutcome(node_id="main", status="done", commands=[record])]
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.client.load_topology_cluster", return_value=saved),
            patch("meridian.commands.client.access_panel", return_value=panel),
            patch("meridian.node_commands.cut_off_scope", return_value=scope),
            patch("meridian.commands.client.remove_connection_page", return_value=""),
            patch("meridian.commands.client.publish_connection_pages", return_value=[links]),
            patch("meridian.commands.client.persist_client_state", return_value=""),
            patch("meridian.node_commands.wait_commands", return_value=outcomes),
        ):
            run_reissue("person-a", yes=True, cut_off=True)
        result = json.loads(capsys.readouterr().out)
        assert result["data"]["client"]["subscription_url"] == links.subscription_url
        assert result["data"]["cut_off"][0]["status"] == "done"
        panel.revoke_user_subscription_with_cut_off.assert_called_once_with("u-a")
        panel.revoke_user_subscription.assert_not_called()
    finally:
        set_json_mode(False)


def test_declining_the_cut_off_confirmation_changes_no_credentials_or_pages(tmp_home) -> None:
    scope = CutOffPlan(nodes=[CutOffNode(node_id="main", inbound_tags=["vless-reality"])])
    with (
        patch("meridian.commands.client.load_topology_cluster", return_value=cluster()),
        patch("meridian.node_commands.cut_off_scope", return_value=scope),
        patch("meridian.commands.client.confirm", return_value=False) as confirm,
        patch("meridian.commands.client.access_panel") as panel,
        patch("meridian.commands.client.remove_connection_page") as page,
        pytest.raises(typer.Exit),
    ):
        run_reissue("person-a", cut_off=True)
    assert "main" in confirm.call_args.args[0] and "every open session" in confirm.call_args.args[0]
    panel.assert_not_called()
    page.assert_not_called()
