"""A real service and saved fleet for immediate people commands and handover regressions."""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from meridian import __version__
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control.models import ApplyFenceRecord, RevisionProposal, RevisionResult
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.topology import single_exit_intent

NOW = 1793534400.0


def configured(url: str) -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, sub_path="pages"),
        topology_intent=single_exit_intent(),
        fleet_id=FLEET_ID,
    )


def legacy_name(database, username: str, display_name: str) -> None:
    """Retain a pre-26 collision that current name writes correctly refuse."""
    with sqlite3.connect(database) as db:
        db.execute("UPDATE users SET display_name = ? WHERE username = ?", (display_name, username))


def committed_people_revision(client: ControlClient, cluster: ClusterConfig) -> None:
    """The service committed adoption, before the CLI saved the local finish."""
    record = client.propose_revision(
        RevisionProposal(
            intent=cluster.topology_intent.to_data(), plan_hash=cluster.active_plan_hash, review_hash="b" * 64
        )
    )
    client.approve_revision(record.number)
    client.record_revision_result(record.number, RevisionResult(status="applied"))
    client.swap_fence(
        client.fence().version,
        ApplyFenceRecord(
            fleet_id=FLEET_ID,
            generation=cluster.active_generation,
            plan_hash=cluster.active_plan_hash,
            revision=record.number,
        ),
    )


@pytest.fixture
def people_cli(tmp_home, monkeypatch):
    with serve_control(tmp_home / "service", clock=lambda: NOW, version=__version__) as url:
        cluster = configured(url)
        cluster.save()
        monkeypatch.setattr("meridian.commands.backup.pull_after_mutations", lambda *_args, **_kwargs: None)
        with ControlClient(url, ADMIN_TOKEN) as client:
            yield CliRunner(), cluster, client
