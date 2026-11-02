"""Undo keeps reviewed grants and previews the actual inverse access change."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands import client_bulk
from meridian.console import set_json_mode
from meridian.control.client import ControlClient, add_mutation_listener
from meridian.control.errors import ServiceError
from meridian.control.models import NodeSpec, ServiceSettings, UserCreate
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from meridian.i18n import t
from tests.support.control_service import ADMIN_TOKEN
from tests.support.text import unstyle
from tests.support.token_service import serve_tokens
from tests.support.topology import single_exit_intent
from tests.test_access_profiles import HY2, POLICY, REALITY, configure_profiles, spec
from tests.test_access_profiles import service as service


@pytest.fixture(autouse=True)
def command_modes(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)


def assign(service, before: str, after: str, names=("person", "other")):
    for name in names:
        service.create_user(UserCreate(username=name, profiles=[before]))
    review = service.bulk.preview(
        BulkPreviewRequest(action=BulkAction(action="set-profiles", profiles=[after]), usernames=list(names))
    )
    return service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))


def change_topology(service, kind: str):
    if kind == "nodes":
        service.put_node_spec("extra", NodeSpec(resources=[]))
    else:
        policy = {"udp": [HY2]} if kind == "missing-profile" else {"limited": [], "udp": [HY2]}
        service.put_settings(
            ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": policy})
        )


@pytest.mark.parametrize("kind", ["grants", "nodes", "missing-profile"])
def test_undo_refuses_changed_access_and_names_every_stale_person_without_writing(service, kind):
    configure_profiles(service)
    receipt = assign(
        service,
        "limited" if kind == "missing-profile" else "default",
        "default" if kind == "missing-profile" else "limited",
    )
    change_topology(service, kind)
    before = service._db.total_changes
    with pytest.raises(ServiceError) as caught:
        service.bulk.undo(receipt.id)
    assert (caught.value.code, caught.value.status) == ("stale_people", 409)
    assert set(caught.value.subjects) == {"person", "other"}
    assert "person" in str(caught.value) and "other" in str(caught.value)
    assert service._db.total_changes == before
    assert service.bulk.receipt(receipt.id).undone_at is None


@pytest.mark.parametrize("kind", ["grants", "nodes", "missing-profile"])
def test_cli_undo_dry_run_checks_live_access_preconditions(tmp_path: Path, tmp_home: Path, kind: str):
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        receipt = assign(
            fleet.service,
            "limited" if kind == "missing-profile" else "default",
            "default" if kind == "missing-profile" else "limited",
        )
        change_topology(fleet.service, kind)
        cluster = ClusterConfig(
            panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
        )
        before = fleet.service._db.total_changes
        with patch("meridian.commands.client_bulk.load_topology_cluster", return_value=cluster):
            reply = CliRunner().invoke(app, ["client", "bulk", "undo", receipt.id, "--dry-run", "--json"])
        assert reply.exit_code == 2, reply.output
        payload = json.loads(reply.stdout)
        assert "person" in payload["errors"][0]["message"] and "other" in payload["errors"][0]["message"]
        assert fleet.service._db.total_changes == before
        assert fleet.service.bulk.receipt(receipt.id).undone_at is None


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize(
    ("before", "after", "gained", "lost"),
    [
        ("default", "limited", [HY2], []),
        ("limited", "default", [], [HY2]),
        ("limited", "udp", [REALITY], [HY2]),
    ],
)
def test_undo_preview_reverses_access_and_discloses_its_own_cut_off_boundary(
    tmp_path: Path, tmp_home: Path, locale: str, before: str, after: str, gained: list[str], lost: list[str]
):
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        receipt = assign(fleet.service, before, after, names=["person"])
        cluster = ClusterConfig(
            panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
        )
        with (
            patch("meridian.commands.client_bulk.load_topology_cluster", return_value=cluster),
            patch("meridian.commands.client_bulk.warn", wraps=client_bulk.warn) as warnings,
        ):
            reply = CliRunner().invoke(app, ["--lang", locale, "client", "bulk", "undo", receipt.id, "--dry-run"])
        assert reply.exit_code == 0, reply.output
        output = " ".join(unstyle(reply.output).split())
        for key, refs in (("gained", gained), ("lost", lost)):
            for ref in refs:
                assert t("profiles." + key) + ": " + ref in output
        assert [call.args[0] for call in warnings.call_args_list] == ([t("profiles.revocation")] if lost else [])
        assert (t("profiles.hard_cut_scope") in output) == bool(lost)
        with patch("meridian.commands.client_bulk.load_topology_cluster", return_value=cluster):
            reply = CliRunner().invoke(app, ["client", "bulk", "undo", receipt.id, "--dry-run", "--json"])
        assert reply.exit_code == 0, reply.output
        result = json.loads(reply.stdout)["data"]
        inverse = result["receipt"]["undo_people"][0]
        assert (inverse["access"]["gained"], inverse["access"]["lost"]) == (gained, lost)
        assert inverse["before"]["profiles"] == [after] and inverse["after"]["profiles"] == [before]
        if lost:
            assert inverse["cut_off"]["nodes"] == [
                {"node_id": "main", "inbound_tags": ["hy2"], "effect": "inbound_reset"}
            ]
        assert not result["applied"]
        assert fleet.service.get_user("person").profiles == [after]
        assert fleet.service.bulk.receipt(receipt.id).undone_at is None


def test_undo_preview_validates_and_projects_without_database_events_or_backup_writes(tmp_path: Path):
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        receipt = assign(fleet.service, "limited", "default", names=["person"])
        before = fleet.service._db.total_changes
        events = fleet.service.events.list()
        writes = []
        stop = add_mutation_listener(writes.append)
        try:
            with ControlClient(fleet.url, ADMIN_TOKEN) as client:
                preview = client.preview_bulk_undo(receipt.id)
        finally:
            stop()
        assert preview.undone_at is None and preview.undo_people[0].access.lost == [HY2]
        assert writes == []
        assert fleet.service._db.total_changes == before
        assert fleet.service.events.list() == events
        assert fleet.service.bulk.receipt(receipt.id) == receipt


def test_receipts_without_saved_access_preconditions_refuse_undo_cleanly(service):
    configure_profiles(service)
    receipt = assign(service, "limited", "default", names=["person"])
    held = json.loads(
        service._db.execute("SELECT versions FROM bulk_receipts WHERE id = ?", (receipt.id,)).fetchone()[0]
    )
    with service._store.transaction():
        service._db.execute(
            "UPDATE bulk_receipts SET versions = ? WHERE id = ?", (json.dumps(held["people"]), receipt.id)
        )
    before = service._db.total_changes
    with pytest.raises(ServiceError) as caught:
        service.bulk.undo(receipt.id, dry_run=True)
    assert caught.value.code == "undo_refused" and "access preconditions" in str(caught.value)
    assert service._db.total_changes == before
    assert service.get_user("person").profiles == ["default"]
