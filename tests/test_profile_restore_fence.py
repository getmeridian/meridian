"""Restore preserves profile history and refuses incompatible snapshots before staging or upload."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from meridian import config, i18n
from meridian.backup import BackupError
from meridian.backup import restore as restore_steps
from meridian.backup.keys import load_device_key
from meridian.backup.profile_restore import review_restored_profile_access
from meridian.backup.restore import ControlRestorer, RestoreSource
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.commands import restore as restore_command
from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload
from meridian.control.client import ControlClient
from meridian.control.restore import read_snapshot_profile_access
from meridian.control.snapshot import Snapshot, decrypt_snapshot
from meridian.core.access_profiles import AccessProfileIntent, profile_declarations_hash
from meridian.core.errors import LocalStateCorruptedError
from meridian.core.topology import SetupIntent
from meridian.reconciler.meridian_drivers import ServiceSettingsDriver
from meridian.reconciler.resources import build_resource_actions
from meridian.servers import ServerRegistry
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.mock_connection import MockConnection
from tests.support.text import unstyle
from tests.test_backup_restore import NewServer
from tests.test_backup_restore import fleet as fleet
from tests.test_backup_restore import new_server as new_server


@pytest.fixture(autouse=True)
def restore_environment(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    monkeypatch.setattr(restore_steps, "service_answers", lambda *_args: False)


def open_snapshot(path: Path) -> Snapshot:
    key = load_device_key()
    assert key is not None
    return decrypt_snapshot(path.read_bytes(), [key.secret])


def changed_snapshot(snapshot: Snapshot, *, profiles=None, declarations_hash=None, history=None, generation=None):
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(snapshot.database)
        if profiles is not None:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('access_profiles', ?)", (json.dumps(profiles),))
        if declarations_hash is not None:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('access_profile_declarations_hash', ?)", (declarations_hash,)
            )
        if history is not None:
            raw = db.execute("SELECT body FROM backup_state WHERE name = 'deployer_state'").fetchone()[0]
            deposit = json.loads(raw)
            cluster = yaml.safe_load(deposit["cluster_yml"])
            cluster["access_profiles_used"] = history
            deposit["cluster_yml"] = yaml.safe_dump(cluster)
            if generation is not None:
                deposit["generation"] = generation
            db.execute("UPDATE backup_state SET body = ? WHERE name = 'deployer_state'", (json.dumps(deposit),))
        db.commit()
        return Snapshot(header=snapshot.header, files={**snapshot.files, "control.db": db.serialize()})
    finally:
        db.close()


def local_profile_intent(intent: SetupIntent) -> SetupIntent:
    return SetupIntent.model_validate(
        {**intent.to_data(), "access_profiles": [{"id": "limited", "grants": [f"exit:{intent.exits[0].id}"]}]}
    )


def restorer_for(cluster: ClusterConfig):
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    target = registry.find("srv-b")
    assert target is not None
    return ControlRestorer(cluster, registry, connection_builder=lambda _entry: MockConnection()), target


@pytest.mark.parametrize("generation", [1, 2], ids=["older-deposit", "same-deposit"])
def test_retained_local_intent_inherits_deposited_profile_history(fleet, generation: int):
    cluster, path = fleet
    snapshot = changed_snapshot(open_snapshot(path), history=True, generation=generation)
    saved = config.CLUSTER_CONFIG.read_bytes()
    restored = restore_command._fleet_state(cluster, snapshot)
    assert restored is cluster
    assert restored.has_used_access_profiles is True
    assert config.CLUSTER_CONFIG.read_bytes() == saved


@pytest.mark.parametrize("mismatch", ["legacy-map", "empty-service-fingerprint", "same-map-different-declarations"])
def test_restore_refuses_snapshot_profile_mismatch_before_staging_upload_or_install(
    fleet, new_server: NewServer, mismatch: str
):
    cluster, path = fleet
    snapshot = open_snapshot(path)
    if mismatch == "legacy-map":
        snapshot = changed_snapshot(snapshot, profiles={"limited": ["inbound:main:main-reality"]})
    else:
        cluster.topology_intent = local_profile_intent(cluster.topology_intent)
        if mismatch == "empty-service-fingerprint":
            snapshot = changed_snapshot(snapshot, declarations_hash=profile_declarations_hash([]))
        else:
            plan = compile_topology(cluster.topology_intent)
            profiles = next(
                row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
            )
            fingerprint = profile_declarations_hash([AccessProfileIntent(id="limited", grants=profiles["limited"])])
            snapshot = changed_snapshot(snapshot, profiles=profiles, declarations_hash=fingerprint)
    restorer, target = restorer_for(cluster)
    saved = config.CLUSTER_CONFIG.read_bytes()
    with pytest.raises(BackupError, match="profile") as refused:
        restorer.run(restorer.review(target, RestoreSource(path, snapshot)), new_server.apply)
    assert refused.value.category == "user"
    assert "matching snapshot" in refused.value.hint and "meridian apply" in refused.value.hint
    assert new_server.uploads == 0 and new_server.server is None and new_server.applies == []
    assert cluster.control_restore is None
    assert config.CLUSTER_CONFIG.read_bytes() == saved
    assert not (config.FLEET_HOME / "restore").exists()


@pytest.mark.parametrize("snapshot_kind", ["legacy", "fingerprinted", "changed-expansion"])
def test_matching_profiles_restore_and_keep_membership_while_adopting_legacy_declarations(
    fleet, new_server: NewServer, snapshot_kind: str
):
    cluster, path = fleet
    before = local_profile_intent(cluster.topology_intent)
    old_plan = compile_topology(before)
    profiles = next(
        row.payload.access_profiles for row in old_plan.resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    declarations = profile_declarations_hash(before.access_profiles)
    snapshot = changed_snapshot(
        open_snapshot(path),
        profiles=profiles,
        declarations_hash=declarations if snapshot_kind != "legacy" else None,
    )
    intent = before
    if snapshot_kind == "changed-expansion":
        data = before.to_data()
        data["exits"][0]["paths"] = [item for item in data["exits"][0]["paths"] if item["protocol"] != "hysteria2"]
        intent = SetupIntent.model_validate(data)
    cluster.topology_intent = intent
    restorer, target = restorer_for(cluster)
    fences = []

    def apply(state, wanted):
        plan = compile_topology(wanted)
        access = review_restored_profile_access(state, plan)
        fences.append(access)
        action = next(row for row in build_resource_actions(plan, 1) if row.resource.logical_id == "settings:service")
        context = SimpleNamespace(
            cluster=state.clone(),
            access_review_hash=access.access_hash if access is not None else None,
            client=lambda: ControlClient(state.panel.url, state.panel.api_token, report_mutations=False),
        )
        context.cluster.panel.url = "https://198.51.100.13/control"
        ServiceSettingsDriver(context).apply(action, None)
        new_server.apply(state, wanted)

    restorer.run(restorer.review(target, RestoreSource(path, snapshot)), apply)
    saved = ClusterConfig.load()
    assert saved.has_used_access_profiles
    assert saved.control_restore.profile_declarations_hash == declarations
    assert (fences[0] is not None) is (snapshot_kind == "legacy")
    with ControlClient(saved.panel.url, saved.panel.api_token, report_mutations=False) as admin:
        assert admin.settings().access_profile_declarations_hash == declarations
        wanted = next(
            row.payload.access_profiles
            for row in compile_topology(intent).resources
            if isinstance(row.payload, ServiceSettingsPayload)
        )
        assert admin.settings().access_profiles == wanted
        assert all(user.profiles == ["default"] for user in admin.list_users())


def test_snapshot_history_is_saved_when_the_library_restorer_keeps_a_profile_free_intent(fleet, new_server: NewServer):
    cluster, path = fleet
    snapshot = changed_snapshot(open_snapshot(path), history=True)
    restorer, target = restorer_for(cluster)
    restorer.run(restorer.review(target, RestoreSource(path, snapshot)), new_server.apply)
    assert ClusterConfig.load().has_used_access_profiles is True


@pytest.mark.parametrize("stage", ["staged", "installed"])
def test_resumed_restore_refuses_a_profile_edit_after_its_snapshot_review(
    fleet, new_server: NewServer, monkeypatch: pytest.MonkeyPatch, stage: str
):
    cluster, path = fleet
    restorer, target = restorer_for(cluster)
    source = RestoreSource(path, open_snapshot(path))
    if stage == "staged":
        monkeypatch.setattr(restore_steps, "upload_database", MagicMock(side_effect=BackupError("Upload interrupted.")))
    else:
        new_server.fail_apply = True
    with pytest.raises(BackupError):
        restorer.run(restorer.review(target, source), new_server.apply)
    saved = ClusterConfig.load()
    assert saved.control_restore.step == stage
    assert saved.control_restore.profile_declarations_hash == profile_declarations_hash([])
    saved.topology_intent = local_profile_intent(saved.topology_intent)
    resumed, target = restorer_for(saved)
    uploaded = new_server.uploads
    with pytest.raises(BackupError, match="profiles"):
        resumed.review(target, RestoreSource(path, None))
    assert new_server.uploads == uploaded


def test_a_legacy_snapshot_cannot_approve_changed_declarations_between_review_and_run(fleet, new_server: NewServer):
    cluster, path = fleet
    intent = local_profile_intent(cluster.topology_intent)
    plan = compile_topology(intent)
    profiles = next(
        row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    source = RestoreSource(path, changed_snapshot(open_snapshot(path), profiles=profiles))
    cluster.topology_intent = intent
    restorer, target = restorer_for(cluster)
    review = restorer.review(target, source)
    data = intent.to_data()
    data["access_profiles"][0]["grants"] = profiles["limited"]
    cluster.topology_intent = SetupIntent.model_validate(data)
    with pytest.raises(BackupError, match="profiles"):
        restorer.run(review, new_server.apply)
    assert new_server.uploads == 0 and new_server.server is None


def test_restore_keeps_the_reviewed_fingerprint_when_mutable_grant_lists_change(fleet, new_server: NewServer):
    cluster, path = fleet
    intent = local_profile_intent(cluster.topology_intent)
    plan = compile_topology(intent)
    profiles = next(
        row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    cluster.topology_intent = intent
    source = RestoreSource(path, changed_snapshot(open_snapshot(path), profiles=profiles))
    restorer, target = restorer_for(cluster)
    review = restorer.review(target, source)
    intent.access_profiles[0].grants[:] = profiles["limited"]
    review.intent.access_profiles[0].grants[:] = profiles["limited"]
    with pytest.raises(BackupError, match="profiles"):
        restorer.run(review, new_server.apply)
    assert new_server.uploads == 0 and new_server.server is None


@pytest.mark.parametrize("during_preview", [False, True], ids=["already-fingerprinted", "legacy-review-race"])
def test_restored_service_cannot_receive_an_automatic_review_for_different_declarations(
    tmp_path: Path, during_preview: bool
):
    from meridian.cluster import PanelConfig
    from meridian.control.models import ServiceSettings
    from tests.support.topology import single_exit_intent

    intent = local_profile_intent(single_exit_intent())
    plan = compile_topology(intent)
    profiles = next(
        row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    changed = profile_declarations_hash([AccessProfileIntent(id="limited", grants=profiles["limited"])])
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        settings = ServiceSettings(profile_title="Meridian", access_profiles=profiles)
        admin.put_settings(settings)
        replacement = ServiceSettings.model_validate(
            {**settings.to_data(), "access_profile_declarations_hash": changed}
        )

        def replace():
            admin.put_settings(replacement, expected_access_hash=admin.access_profiles().access_hash)

        original = ControlClient.preview_access_profiles

        def raced_preview(client, request):
            replace()
            return original(client, request)

        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        if not during_preview:
            replace()
        preview = (
            patch.object(ControlClient, "preview_access_profiles", side_effect=raced_preview, autospec=True)
            if during_preview
            else nullcontext()
        )
        with preview, pytest.raises(BackupError, match="profiles"):
            review_restored_profile_access(cluster, plan)
        assert admin.settings() == replacement


def test_reading_profile_evidence_from_a_snapshot_before_memberships_does_not_migrate_it(fleet):
    _cluster, path = fleet
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(open_snapshot(path).database)
        db.execute("DROP TABLE user_access_memberships")
        db.execute("UPDATE meta SET value = '22' WHERE key = 'schema_version'")
        db.commit()
        raw = db.serialize()
        state = read_snapshot_profile_access(raw)
        assert state.declarations_hash is None and state.access_profiles == {}
        assert state.has_non_default_memberships is False
        assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "22"
    finally:
        db.close()


@pytest.mark.parametrize("value", ["not-a-hash", 0, False, [], {}, "x" * 64])
def test_malformed_restore_profile_review_is_refused_when_loading_cluster(tmp_path: Path, value):
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump({"version": 4, "control_restore": {"profile_declarations_hash": value}}))
    with pytest.raises(LocalStateCorruptedError, match="profile_declarations_hash"):
        ClusterConfig.load(path)


@pytest.mark.parametrize("value", [[], "invalid", False, 0])
def test_malformed_restore_record_cannot_erase_its_profile_review(tmp_path: Path, value):
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump({"version": 4, "control_restore": value}))
    with pytest.raises(LocalStateCorruptedError, match="control_restore must be a mapping"):
        ClusterConfig.load(path)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_refuses_snapshot_profile_mismatch_in_the_operators_language_before_target_access(
    fleet, new_server: NewServer, monkeypatch: pytest.MonkeyPatch, locale: str
):
    _cluster, path = fleet
    snapshot = changed_snapshot(open_snapshot(path), profiles={"limited": []})
    monkeypatch.setattr(restore_command, "_open_snapshot", lambda *_args, **_kw: RestoreSource(path, snapshot))
    with (
        patch("meridian.commands.resolve.resolve_server") as resolve,
        patch("meridian.commands.server.register_server") as register,
    ):
        reply = CliRunner().invoke(app, ["--lang", locale, "restore", "--to", "198.51.100.13", "--yes"])
    assert reply.exit_code == 2, reply.output
    text = " ".join(unstyle(reply.output).split())
    assert i18n.t("profiles.restore_mismatch") in text
    assert "meridian apply" in text and "--context NAME" in text
    resolve.assert_not_called()
    register.assert_not_called()
    assert new_server.uploads == 0 and new_server.server is None


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_unreadable_snapshot_profile_data_is_a_native_error_before_restore(fleet, new_server: NewServer, locale: str):
    cluster, path = fleet
    i18n.set_locale(locale)
    snapshot = changed_snapshot(open_snapshot(path), declarations_hash="invalid")
    restorer, target = restorer_for(cluster)
    with pytest.raises(BackupError) as refused:
        restorer.review(target, RestoreSource(path, snapshot))
    assert str(refused.value) == i18n.t("profiles.restore_state_unreadable")
    assert refused.value.hint == i18n.t("profiles.restore_state_retry")
    assert refused.value.category == "user"
    assert new_server.uploads == 0 and new_server.server is None
