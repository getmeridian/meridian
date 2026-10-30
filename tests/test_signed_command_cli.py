"""Public command flags and typed outcomes for node restarts and urgent cut-offs."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_reissue, run_remove
from meridian.console import set_json_mode
from meridian.control.client import ControlServiceError, ControlServiceUncertain
from meridian.control.command_models import (
    CommandUnitTarget,
    CutOffNode,
    CutOffPlan,
    NodeCommandOutcome,
    NodeCommandTargets,
)
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


def test_node_restart_help_names_the_core_and_all_unit_scopes() -> None:
    reply = CliRunner().invoke(app, ["node", "restart", "--help"])
    assert reply.exit_code == 0
    assert all(flag in reply.output for flag in ("--core", "--all", "--json", "--yes"))
    assert "Xray" in reply.output and "managed" in reply.output


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

    def applied(*args, **kwargs):
        events.append("apply")
        kwargs["user_cut_off"]["person-a"].extend(outcomes[0].commands)

    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.client.load_topology_cluster", return_value=saved),
            patch("meridian.commands.client_v4.access_panel", return_value=panel),
            patch("meridian.node_commands.cut_off_scope", return_value=scope),
            patch(
                "meridian.commands.client_v4.apply_reviewed_intent",
                side_effect=applied,
            ),
            patch(
                "meridian.node_commands.wait_commands",
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


@pytest.mark.parametrize(
    ("all_units", "status", "exit_code"),
    [(False, "succeeded", 0), (True, "succeeded", 0), (False, "failed", 4), (False, "expired", 3)],
)
def test_node_restart_selects_the_requested_units_and_emits_typed_results(
    tmp_home, capsys, all_units, status, exit_code
) -> None:
    from meridian.commands.node_restart import run_restart

    topology = SimpleNamespace(nodes=[SimpleNamespace(name="main", uuid="main", ip="198.51.100.10")])
    targets = NodeCommandTargets(
        node_id="main",
        units=[
            CommandUnitTarget(resource_id="unit:netcore", name="netcore.service", core=True),
            CommandUnitTarget(resource_id="unit:forward", name="netfwd-test.service", core=False),
        ],
        inbounds=[],
        capabilities=["restart_unit"],
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.command_targets.return_value = targets
    issued = {}

    def issue(node_id, request):
        index = len(issued) + 1
        record = command_record(node_id, index=index, unit=request.resource_id)
        issued[record.command.command_id] = command_record(node_id, status, index=index, unit=request.resource_id)
        return record

    client.issue_command.side_effect = issue
    client.command.side_effect = lambda node_id, command_id: issued[command_id]
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.node_restart.load_topology_cluster", return_value=cluster()),
            patch("meridian.commands.node_restart.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node_restart.ControlClient", return_value=client),
        ):
            if exit_code:
                with pytest.raises(typer.Exit) as raised:
                    run_restart("main", all_units=all_units, yes=True)
                assert raised.value.exit_code == exit_code
            else:
                run_restart("main", all_units=all_units, yes=True)
        body = json.loads(capsys.readouterr().out)
        assert body["exit_code"] == exit_code
        assert body["data"]["scope"] == ("all" if all_units else "core")
        result = body["data"]["nodes"][0]
        assert result["status"] == {"succeeded": "done", "failed": "failed", "expired": "timed_out"}[status]
        assert {record["command"]["resource_id"] for record in result["commands"]} == (
            {"unit:netcore", "unit:forward"} if all_units else {"unit:netcore"}
        )
    finally:
        set_json_mode(False)


def test_conflicting_restart_scopes_are_refused_before_any_service_call(tmp_home) -> None:
    from meridian.commands.node_restart import run_restart

    with patch("meridian.commands.node_restart.ControlClient") as client, pytest.raises(typer.Exit) as raised:
        run_restart("main", core=True, all_units=True, yes=True)
    assert raised.value.exit_code == 2
    client.assert_not_called()


def test_review_3_3_an_uncertain_second_restart_never_downgrades_an_earlier_failure(tmp_home, capsys) -> None:
    from meridian.commands.node_restart import run_restart

    client = MagicMock()
    client.__enter__.return_value = client
    client.command_targets.return_value = NodeCommandTargets(
        node_id="main",
        units=[
            CommandUnitTarget(resource_id="unit:netcore", name="netcore.service", core=True),
            CommandUnitTarget(resource_id="unit:forward", name="netfwd-test.service", core=False),
        ],
        inbounds=[],
        capabilities=["restart_unit"],
    )
    first = command_record("main", "failed", unit="unit:netcore")
    client.issue_command.side_effect = [first, ControlServiceUncertain("The write may have committed.", retryable=True)]
    topology = SimpleNamespace(nodes=[SimpleNamespace(name="main", uuid="main", ip="198.51.100.10")])
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.node_restart.load_topology_cluster", return_value=cluster()),
            patch("meridian.commands.node_restart.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node_restart.ControlClient", return_value=client),
            pytest.raises(typer.Exit) as raised,
        ):
            run_restart("main", all_units=True, yes=True)
        body = json.loads(capsys.readouterr().out)
        assert raised.value.exit_code == 4 and body["exit_code"] == 4
        assert body["data"]["nodes"][0]["status"] == "failed"
        assert body["data"]["nodes"][0]["commands"][0]["status"] == "failed"
        assert body["warnings"][0]["retryable"] is False
    finally:
        set_json_mode(False)


@pytest.mark.parametrize("command", ["node.restart", "client.remove", "client.reissue"])
@pytest.mark.parametrize(
    ("message", "category", "exit_code"),
    [
        ("The service cannot be reached.", "system", 3),
        ("HTTP 401", "user", 2),
        ("HTTP 403", "user", 2),
        ("HTTP 404", "user", 2),
    ],
)
def test_review_5_1_service_preflight_errors_use_fail_with_the_command_json_envelope(
    command, message, category, exit_code, tmp_home, capsys
) -> None:
    from meridian.commands.node_restart import run_restart

    error = ControlServiceError(message, category=category)
    client = MagicMock()
    client.__enter__.return_value = client
    client.command_targets.side_effect = error
    topology = SimpleNamespace(nodes=[SimpleNamespace(name="main", uuid="main", ip="198.51.100.10")])
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands.client.load_topology_cluster", return_value=cluster()),
            patch("meridian.commands.node_restart.load_topology_cluster", return_value=cluster()),
            patch("meridian.commands.node_restart.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node_restart.ControlClient", return_value=client),
            patch("meridian.node_commands.cut_off_scope", side_effect=error),
            patch("meridian.commands.client_v4.apply_reviewed_intent") as apply,
            patch("meridian.commands.client.remove_connection_page") as page,
            pytest.raises(typer.Exit) as raised,
        ):
            if command == "node.restart":
                run_restart("main", yes=True)
            elif command == "client.remove":
                run_remove("person-a", yes=True, cut_off=True)
            else:
                run_reissue("person-a", yes=True, cut_off=True)
        body = json.loads(capsys.readouterr().out)
        assert raised.value.exit_code == body["exit_code"] == exit_code
        assert body["schema"] == "meridian.output/v1" and body["command"] == command
        assert body["status"] == "failed" and body["errors"][0]["message"] == message
        assert body["errors"][0]["category"] == category
        apply.assert_not_called()
        page.assert_not_called()
    finally:
        set_json_mode(False)
