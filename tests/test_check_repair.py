"""`meridian check --repair`: which server it targets, how it confirms, and what it reports."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.commands import control_repair
from meridian.control.models import ServiceConfig
from meridian.control_artifacts import ServicePackage
from meridian.provision.control_repair import RepairReview
from meridian.provision.steps import StepResult
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import add_relay_to_intent
from meridian.setup.single_server import single_server_intent

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
RELEASE = ServicePackage(requirement="meridian-vpn==4.0.0")
REVIEW = RepairReview(
    config=ServiceConfig(fleet_id=FLEET_ID, admin_token_sha256="a" * 64), package=RELEASE, recorded=RELEASE.identity
)
runner = CliRunner()


def _plain(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


@pytest.fixture
def fleet(servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A control+exit server and a relay into it; the control server's service is down."""
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="exit-a"))
    registry.add(ServerEntry(host="198.51.100.20", name="relay-a"))
    intent = single_server_intent(None, server_ref=registry.find("exit-a").id, sni="www.example.com")
    intent = add_relay_to_intent(
        intent, server_ref=registry.find("relay-a").id, title="relay-a", exit_ref="main", listen_port=8443
    )
    ClusterConfig(topology_intent=intent, fleet_id=FLEET_ID).save()
    mocks = SimpleNamespace(
        conn=MagicMock(local_mode=False, user="root"),
        review=MagicMock(return_value=REVIEW),
        repair=MagicMock(return_value=StepResult(name="Install control service", status="changed")),
        confirm=MagicMock(return_value=False),
        has_tty=MagicMock(return_value=False),
    )
    with (
        patch.object(control_repair, "connection_for_entry", return_value=mocks.conn) as connect,
        patch.object(control_repair, "service_package", return_value=RELEASE),
        patch.object(control_repair, "review_repair", mocks.review),
        patch.object(control_repair, "repair", mocks.repair),
        patch.object(control_repair, "confirm", mocks.confirm),
        patch.object(control_repair, "has_tty", mocks.has_tty),
    ):
        mocks.connect = connect
        yield mocks


def test_repair_targets_the_control_server_and_reports_it_answers(fleet: SimpleNamespace) -> None:
    result = runner.invoke(app, ["check", "--repair", "--yes"])

    output = _plain(result.output)
    assert result.exit_code == 0, output
    assert fleet.connect.call_args.args[0].host == "198.51.100.10"
    assert fleet.review.call_args.kwargs == {"fleet_id": FLEET_ID, "host": "198.51.100.10"}
    fleet.repair.assert_called_once_with(fleet.conn, REVIEW)
    assert "Repair the control service on exit-a (198.51.100.10)" in output
    assert "reinstalls meridian-vpn 4.0.0 under /opt/ctld" in output
    assert "topology, people, node documents, the apply fence, and revisions do not change" in output
    assert "The control service on exit-a (198.51.100.10) answers again" in output


def test_naming_another_server_is_refused_before_ssh(fleet: SimpleNamespace) -> None:
    result = runner.invoke(app, ["check", "relay-a", "--repair", "--yes"])

    output = _plain(result.output)
    assert result.exit_code == 2
    assert "relay-a is not this fleet's control server" in output
    assert "Its server is exit-a (198.51.100.10)" in output
    fleet.connect.assert_not_called()


def test_naming_the_control_server_is_accepted(fleet: SimpleNamespace) -> None:
    result = runner.invoke(app, ["check", "198.51.100.10", "--repair", "--yes"])

    assert result.exit_code == 0, result.output
    fleet.repair.assert_called_once()


def test_without_a_terminal_or_yes_nothing_is_reinstalled(fleet: SimpleNamespace) -> None:
    result = runner.invoke(app, ["check", "--repair"])

    assert result.exit_code == 1
    assert "pass --yes to repair without asking" in _plain(result.output)
    fleet.confirm.assert_called_once_with("Repair the control service now?", default=False)
    fleet.repair.assert_not_called()


def test_a_failed_repair_names_the_cause_and_the_log(fleet: SimpleNamespace) -> None:
    fleet.repair.return_value = StepResult(
        name="Install control service", status="failed", detail="ctld.service did not answer: ImportError"
    )

    result = runner.invoke(app, ["check", "--repair", "--yes"])

    output = _plain(result.output)
    assert result.exit_code == 3
    assert "still not answering: ctld.service did not answer: ImportError" in output
    assert "meridian logs 198.51.100.10 --component panel" in output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["check", "--repair", "--sni", "www.example.com"], "--repair cannot be combined"),
        (["check", "--repair", "--json"], "--repair has no JSON output"),
        (["check", "--yes"], "--yes confirms --repair"),
    ],
    ids=["with-check-flags", "json", "yes-alone"],
)
def test_flags_that_do_not_go_together_are_refused(fleet: SimpleNamespace, args: list[str], message: str) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert message in _plain(result.output)
    fleet.connect.assert_not_called()
