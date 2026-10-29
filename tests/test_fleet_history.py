"""`meridian fleet history`: the control service's revisions, and a plain answer on a Remnawave fleet."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cli_catalog import public_commands
from meridian.cluster import ClusterConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from meridian.control.enrollment import token_sha256
from meridian.control.models import RevisionProposal, RevisionResult
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.core.topology import ControlPlaneIntent
from tests.support.topology import single_exit_intent

runner = CliRunner()
_FLEET = "11111111-1111-4111-8111-111111111111"
_TOKEN = "admin-token-for-tests-only-0123456789"


@pytest.fixture(autouse=True)
def _human_output() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.fixture
def service_url(tmp_path: Path) -> Iterator[str]:
    control = ControlService(tmp_path / "control.db", fleet_id=_FLEET, version="4.0.0")
    server = ControlServer(("127.0.0.1", 0), control, token_sha256(_TOKEN))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _save(backend: str, url: str = "") -> None:
    cluster = ClusterConfig()
    cluster.topology_intent = single_exit_intent(control=ControlPlaneIntent(server_ref="srv-control", backend=backend))
    cluster.panel.url, cluster.panel.api_token = url, _TOKEN if url else ""
    cluster.save()


def _record(url: str) -> None:
    proposal = RevisionProposal(intent={"control": {}}, plan_hash="a" * 64, review_hash="b" * 64)
    with ControlClient(url, _TOKEN) as root:
        first = root.propose_revision(proposal).number
        root.approve_revision(first)
        root.record_revision_result(first, RevisionResult(status="applied"))
        bot = root.create_token("ci-bot", "propose").token
    with ControlClient(url, bot) as ci:
        ci.propose_revision(proposal)


def _json(args: list[str]) -> dict[str, Any]:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_history_lists_revisions_newest_first(tmp_home: Path, service_url: str) -> None:
    _save("meridian", service_url)
    _record(service_url)

    envelope = _json(["fleet", "history", "--json"])

    assert envelope["command"] == "fleet.history" and envelope["data"]["recorded"] is True
    rows = [(item["number"], item["author"]["name"], item["status"]) for item in envelope["data"]["revisions"]]
    assert rows == [(2, "ci-bot", "proposed"), (1, "root", "applied")]
    assert envelope["data"]["revisions"][1]["plan_hash"] == "a" * 64


def test_history_table_shows_number_author_status_and_plan_prefix(tmp_home: Path, service_url: str) -> None:
    _save("meridian", service_url)
    _record(service_url)

    result = runner.invoke(app, ["fleet", "history"])

    assert result.exit_code == 0, result.output
    assert "ci-bot" in result.output and "applied" in result.output
    assert "a" * 12 in result.output and "a" * 13 not in result.output


def test_global_json_flag_reaches_the_hidden_command(tmp_home: Path, service_url: str) -> None:
    _save("meridian", service_url)
    assert _json(["--json", "fleet", "history"])["data"]["revisions"] == []


def test_a_remnawave_fleet_records_no_revisions(tmp_home: Path) -> None:
    _save("remnawave")

    human = runner.invoke(app, ["fleet", "history"])
    assert human.exit_code == 0 and "only on control-service fleets" in human.output
    assert _json(["fleet", "history", "--json"])["data"] == {"recorded": False, "revisions": []}


def test_history_stays_hidden_while_the_control_backend_is() -> None:
    assert "fleet.history" not in public_commands()
