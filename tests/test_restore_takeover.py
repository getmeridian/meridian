"""`meridian restore` into a new context takes a stale copy's servers over and leaves that copy read-only."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from meridian import config
from meridian.backup import restore as restore_steps
from meridian.backup.keys import load_device_key
from meridian.backup.pull import pull_snapshot
from meridian.backup.restore import ControlRestorer, RestoreSource
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands import context as context_command
from meridian.contexts import Superseded, mark_superseded, save_context, superseded
from meridian.control.client import ControlClient
from meridian.control.snapshot import decrypt_snapshot
from meridian.core.errors import LocalStateError
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.fleet_contexts import refuse_if_superseded, refuse_servers_claimed_elsewhere, summarize_context
from meridian.handoff import HandoffLinks
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.mock_connection import MockConnection
from tests.test_contexts import CONTEXT_BOUND

OLD, NEW, HOP = "198.51.100.12", "198.51.100.13", "198.51.100.21"
SECRET_PATH, SUB_PATH = "a1b2c3d4e5f6a7b8c9d0e1f2", "connect7f3a9b2c"


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An unpinned install home, so other contexts exist to claim servers."""
    home = tmp_path / "meridian"
    home.mkdir()
    monkeypatch.delenv("MERIDIAN_HOME", raising=False)
    monkeypatch.delenv("MERIDIAN_CONTEXT", raising=False)
    monkeypatch.setattr(config, "PINNED_HOME", False)
    monkeypatch.setattr(config, "INSTALL_HOME", home)
    monkeypatch.setattr(config, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(config, "PREFERENCES_FILE", home / "preferences.json")
    monkeypatch.setattr(config, "MERIDIAN_SSH_KEY_FILE", home / "ssh" / "meridian_ed25519")
    for name in CONTEXT_BOUND:
        monkeypatch.setattr(config, name, getattr(config, name))
    config.select_context()
    yield home


def _intent() -> SetupIntent:
    """A control server that is also the exit, plus a relay hop on a third server that stays put."""
    intent = single_server_intent(None, server_ref="srv-a", address=OLD)
    reality = next(path for path in intent.exits[0].paths if path.protocol == "reality")
    relay = TransparentRelayIntent(
        id="relay-a", hop_server_refs=["srv-hop"], exit_ref=intent.exits[0].id, protocol_path_ref=reality.id
    )
    access = intent.access.model_copy(update={"users": ["alice"]})
    return intent.model_copy(update={"transparent_relays": [relay], "access": access})


@pytest.fixture
def stale(install: Path, tmp_path: Path) -> Path:
    """`default` holds a copy of the fleet (and its snapshot); `fresh` holds the snapshot's newer copy."""
    with serve_control(tmp_path / "old-server") as url:
        with ControlClient(url, ADMIN_TOKEN) as client:
            client.put_user("alice")
        ClusterConfig(
            panel=PanelConfig(
                url=url, api_token=ADMIN_TOKEN, server_ip=OLD, secret_path=SECRET_PATH, sub_path=SUB_PATH
            ),
            topology_intent=_intent(),
            fleet_id=FLEET_ID,
            active_generation=2,
        ).save()
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        registry.add(ServerEntry(host=OLD, name="old", id="srv-a", auth_state="validated"))
        registry.add(ServerEntry(host=NEW, name="new", id="srv-b", auth_state="validated"))
        registry.add(ServerEntry(host=HOP, name="hop", id="srv-hop", auth_state="validated"))
        snapshot = pull_snapshot(ClusterConfig.load()).path
    fresh = install / "contexts" / "fresh"
    fresh.mkdir(parents=True)
    for name in ("cluster.yml", "servers.json", "device.key"):
        shutil.copy2(install / name, fresh / name)
    config.select_context("fresh")
    cluster = ClusterConfig.load()
    cluster.active_generation = 3  # the snapshot's copy is newer than the one left in `default`
    cluster.save()
    return snapshot


class NewServer:
    """Records the restore's apply, which first runs the ownership check the reviewed apply runs."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    def apply(self, cluster: ClusterConfig, intent: SetupIntent) -> None:
        refuse_servers_claimed_elsewhere([NEW, HOP])
        self.applied.append(config.CONTEXT)


@pytest.fixture
def new_server(monkeypatch: pytest.MonkeyPatch) -> NewServer:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    monkeypatch.setattr(restore_steps, "upload_database", lambda conn, path, sha256, *, fleet_id: None)
    monkeypatch.setattr(restore_steps, "hand_fence_to_service", lambda conn, cluster: None)
    monkeypatch.setattr(restore_steps, "install_service", lambda plan, cluster, entry, conn: None)
    monkeypatch.setattr(restore_steps, "verify_restored", lambda cluster, epoch: None)
    monkeypatch.setattr(restore_steps, "ssh_answers", lambda entry: False)
    monkeypatch.setattr(restore_steps, "publish_pages", lambda cluster, names: [HandoffLinks(name) for name in names])
    return NewServer()


def _restorer() -> tuple[ControlRestorer, ServerEntry]:
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    target = registry.find("srv-b")
    assert target is not None
    return ControlRestorer(ClusterConfig.load(), registry, connection_builder=lambda entry: MockConnection()), target


def _source(path: Path) -> RestoreSource:
    key = load_device_key()
    assert key is not None
    return RestoreSource(path, decrypt_snapshot(path.read_bytes(), [key.secret]))


def test_the_review_names_the_context_that_loses_which_servers(
    stale: Path, new_server: NewServer, capsys: pytest.CaptureFixture[str]
) -> None:
    from meridian.commands.restore import _render

    restorer, target = _restorer()

    review = restorer.review(target, _source(stale))
    _render(review, ServerRegistry(config.SERVER_PROFILES_FILE))

    assert dict(review.takeover) == {"default": (HOP,)}
    shown = " ".join(capsys.readouterr().err.split())
    assert f"Takes over from context default (an older copy of this fleet): {HOP}" in shown
    assert "meridian context remove default" in shown
    assert superseded(config.INSTALL_HOME) is None, "nothing changes at review"


def test_without_a_takeover_the_stale_copy_still_refuses_the_restore(stale: Path, new_server: NewServer) -> None:
    with pytest.raises(LocalStateError, match="context 'default'"):
        refuse_servers_claimed_elsewhere([HOP])


def test_running_the_restore_supersedes_the_stale_copy_and_its_apply_passes(stale: Path, new_server: NewServer) -> None:
    restorer, target = _restorer()
    review = restorer.review(target, _source(stale))

    restorer.run(review, new_server.apply)

    assert new_server.applied == ["fresh"]
    marker = superseded(config.INSTALL_HOME)
    assert marker is not None and marker.by == "fresh" and marker.at
    assert (config.INSTALL_HOME / "cluster.yml").exists(), "the stale copy's files stay"
    assert summarize_context("default", current="fresh").superseded_by == "fresh"


def test_a_superseded_context_refuses_changes_and_only_reads(install: Path) -> None:
    mark_superseded(install, Superseded(by="fresh", at="2026-10-29T12:00:00Z"))

    with pytest.raises(LocalStateError, match="now lives in context 'fresh'") as error:
        refuse_if_superseded()
    assert "meridian context remove default" in (error.value.hint or "")

    result = CliRunner().invoke(app, ["client", "add", "bob"])
    assert result.exit_code == 2 and "now lives in context 'fresh'" in result.output

    context_command._gate_superseded("client.list")  # reads warn, never stop


def test_a_superseded_context_claims_none_of_its_servers(install: Path) -> None:
    registry = ServerRegistry(install / "servers.json")
    registry.add(ServerEntry(host=HOP, name="hop", id="srv-hop"))
    ClusterConfig(topology_intent=_intent().model_copy(update={"control": _intent().control})).save()
    (install / "contexts" / "fresh").mkdir(parents=True)
    config.select_context("fresh")
    with pytest.raises(LocalStateError):
        refuse_servers_claimed_elsewhere([HOP])

    mark_superseded(install, Superseded(by="fresh", at="2026-10-29T12:00:00Z"))

    refuse_servers_claimed_elsewhere([HOP])


@pytest.mark.parametrize(
    ("body", "by"),
    [('{"by": "fresh", "at": "2026-10-29T12:00:00Z"}\n', "fresh"), ("not json", ""), ("[1]", "")],
    ids=["written", "malformed", "not-an-object"],
)
def test_the_marker_round_trips_and_an_unreadable_one_still_counts(tmp_path: Path, body: str, by: str) -> None:
    (tmp_path / "superseded.json").write_text(body, encoding="utf-8")

    marker = superseded(tmp_path)

    assert marker is not None and marker.by == by


def test_mark_superseded_writes_what_superseded_reads(tmp_path: Path) -> None:
    mark_superseded(tmp_path, Superseded(by="fresh", at="2026-10-29T12:00:00Z"))

    assert superseded(tmp_path) == Superseded(by="fresh", at="2026-10-29T12:00:00Z")
    assert superseded(tmp_path / "missing") is None


def test_a_superseded_default_context_can_drop_its_copy_and_keeps_the_persons_files(install: Path) -> None:
    (install / "ssh").mkdir()
    (install / "ssh" / "meridian_ed25519").write_text("key", encoding="utf-8")
    (install / "snapshots").mkdir()
    (install / "backups").mkdir()
    ClusterConfig(topology_intent=_intent()).save()
    mark_superseded(install, Superseded(by="fresh", at="2026-10-29T12:00:00Z"))
    (install / "contexts" / "fresh").mkdir(parents=True)
    save_context(install, "fresh")
    config.select_context()

    context_command.run_remove("default", yes=True)

    assert not (install / "cluster.yml").exists() and not (install / "snapshots").exists()
    assert superseded(install) is None
    assert (install / "ssh" / "meridian_ed25519").exists() and (install / "backups").exists()


def test_the_default_context_still_cannot_be_removed_while_it_holds_its_fleet(install: Path) -> None:
    (install / "contexts" / "fresh").mkdir(parents=True)
    save_context(install, "fresh")
    config.select_context()

    with pytest.raises(typer.Exit):
        context_command.run_remove("default", yes=True)


def _command_review(takeover: dict[str, tuple[str, ...]]) -> SimpleNamespace:
    return SimpleNamespace(takeover=takeover)


@pytest.mark.parametrize(
    ("yes", "tty", "runs"),
    [(True, False, True), (False, False, False)],
    ids=["yes-accepts", "no-terminal-refuses"],
)
def test_the_takeover_needs_yes_or_a_terminal(
    install: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], yes: bool, tty: bool, runs: bool
) -> None:
    from meridian.commands import restore as restore_command

    ran: list[bool] = []

    class Restorer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def review(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
            return _command_review({"default": (HOP,)})

        def run(self, *args: Any) -> Any:
            ran.append(True)
            return SimpleNamespace()

    monkeypatch.setattr(restore_command, "ControlRestorer", Restorer)
    monkeypatch.setattr(restore_command, "load_cluster", ClusterConfig)
    monkeypatch.setattr(
        restore_command, "_open_snapshot", lambda explicit, *, recovery_key: SimpleNamespace(snapshot=None)
    )
    monkeypatch.setattr(restore_command, "_fleet_state", lambda cluster, snapshot: cluster)
    monkeypatch.setattr(restore_command, "_render", lambda review, registry: None)
    monkeypatch.setattr(restore_command, "_done", lambda outcome: None)
    monkeypatch.setattr(restore_command, "has_tty", lambda: tty)
    monkeypatch.setattr("meridian.commands.resolve.resolve_server", lambda registry, **kwargs: None)
    monkeypatch.setattr("meridian.commands.server.register_server", lambda registry, resolved, *, name: None)
    monkeypatch.setattr(restore_command, "confirm", lambda question, default: False)

    if runs:
        restore_command.run("198.51.100.13", yes=yes)
    else:
        with pytest.raises(typer.Exit):
            restore_command.run("198.51.100.13", yes=yes)

    assert ran == ([True] if runs else [])
    if not runs:
        assert "pass --yes" in capsys.readouterr().err
