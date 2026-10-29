"""`meridian restore`: a snapshot becomes the control service on a new server, checkpointed and resumable."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from meridian import config
from meridian.backup import BackupError
from meridian.backup import restore as restore_steps
from meridian.backup.keys import load_device_key
from meridian.backup.pull import pull_snapshot
from meridian.backup.restore import ControlRestorer, RestoreSource, upload_database
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control.enrollment import token_sha256
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot
from meridian.core.topology import SetupIntent
from meridian.handoff import HandoffLinks
from meridian.node_deploy import panel_base_url
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.mock_connection import MockConnection

OLD, NEW = "198.51.100.12", "198.51.100.13"
SECRET_PATH, SUB_PATH = "a1b2c3d4e5f6a7b8c9d0e1f2", "connect7f3a9b2c"


@dataclass
class NewServer:
    """The new control server: uploads land in a directory, the 'bootstrap' serves them on loopback."""

    root: Path
    uploads: int = 0
    applies: list[SetupIntent] = field(default_factory=list)
    fail_apply: bool = False
    server: ControlServer | None = None

    def upload(self, conn: Any, path: Path, sha256: str, *, fleet_id: str) -> None:
        self.uploads += 1
        (self.root / "control.db").write_bytes(path.read_bytes())

    def install(self, plan: Any, cluster: ClusterConfig, entry: ServerEntry, conn: Any) -> None:
        assert cluster.panel.server_ip == entry.host == NEW
        service = ControlService(self.root / "control.db", fleet_id=FLEET_ID, version="4.0.0")
        self.server = ControlServer(("127.0.0.1", 0), service, token_sha256(cluster.panel.api_token))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        cluster.panel.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def apply(self, cluster: ClusterConfig, intent: SetupIntent) -> None:
        if self.fail_apply:
            self.fail_apply = False
            raise BackupError("apply failed")
        self.applies.append(intent)

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


@pytest.fixture
def new_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NewServer]:
    root = tmp_path / "new-server"
    root.mkdir()
    server = NewServer(root)
    monkeypatch.setattr(restore_steps, "upload_database", server.upload)
    monkeypatch.setattr(restore_steps, "install_service", server.install)
    monkeypatch.setattr(restore_steps, "ssh_answers", lambda entry: False)
    monkeypatch.setattr(
        restore_steps,
        "publish_pages",
        lambda cluster, names: [
            HandoffLinks(name, subscription_url=cluster.panel.subscription_url(f"token-{name}")) for name in names
        ],
    )
    yield server
    server.close()


def _intent(control_domain: str = "") -> SetupIntent:
    intent = single_server_intent(None, server_ref="srv-a", address=OLD, control="meridian")
    return intent.model_copy(
        update={
            "access": intent.access.model_copy(update={"users": ["alice", "bob"]}),
            "control": intent.control.model_copy(update={"public_hostname": control_domain}),
        }
    )


@pytest.fixture
def fleet(tmp_home: Path, tmp_path: Path) -> Iterator[tuple[ClusterConfig, Path]]:
    """A service fleet with a pulled snapshot, whose control server then went dark."""
    with serve_control(tmp_path / "old-server") as url:
        with ControlClient(url, ADMIN_TOKEN) as client:
            client.put_user("alice")
            client.put_user("bob")
        cluster = ClusterConfig(
            panel=PanelConfig(
                url=url, api_token=ADMIN_TOKEN, server_ip=OLD, secret_path=SECRET_PATH, sub_path=SUB_PATH
            ),
            topology_intent=_intent(),
            fleet_id=FLEET_ID,
            active_generation=2,
        )
        cluster.save()
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        registry.add(ServerEntry(host=OLD, name="old", id="srv-a", auth_state="validated"))
        registry.add(ServerEntry(host=NEW, name="new", id="srv-b", auth_state="validated"))
        pulled = pull_snapshot(ClusterConfig.load())
    cluster = ClusterConfig.load()
    cluster.panel.url = panel_base_url(OLD, "", SECRET_PATH).rstrip("/")
    cluster.save()
    yield cluster, pulled.path


def _source(path: Path) -> RestoreSource:
    key = load_device_key()
    assert key is not None
    return RestoreSource(path, decrypt_snapshot(path.read_bytes(), [key.secret]))


def _restorer(cluster: ClusterConfig) -> tuple[ControlRestorer, ServerEntry]:
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    target = registry.find("srv-b")
    assert target is not None
    return ControlRestorer(cluster, registry, connection_builder=lambda entry: MockConnection()), target


def test_a_restore_rebuilds_the_service_at_the_next_epoch(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)

    review = restorer.review(target, _source(snapshot))
    outcome = restorer.run(review, new_server.apply)

    assert outcome.fleet_epoch == 2 and outcome.links_change
    assert review.moved_exits == tuple(exit_.id for exit_ in _intent().exits)
    saved = ClusterConfig.load()
    assert saved.control_restore is not None and saved.control_restore.step == "done"
    assert saved.topology_intent is not None and saved.topology_intent.control.server_ref == "srv-b"
    assert {exit_.server_ref for exit_ in saved.topology_intent.exits} == {"srv-b"}
    assert saved.panel.server_ip == NEW and saved.panel.api_token != ADMIN_TOKEN
    assert not (config.FLEET_HOME / "restore" / "control.db").exists()
    with ControlClient(saved.panel.url, saved.panel.api_token) as client:
        assert client.health().fleet_epoch == 2
        assert [user.username for user in client.list_users()] == ["alice", "bob"]
    with pytest.raises(Exception, match="refused the saved admin token"):
        ControlClient(saved.panel.url, ADMIN_TOKEN).health()
    assert [links.username for links in outcome.links] == ["alice", "bob"]
    assert all(links.subscription_url and OLD not in links.subscription_url for links in outcome.links)
    assert len(new_server.applies) == 1


def test_the_review_says_a_bare_ip_fleets_links_change_and_a_domain_fleets_do_not(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)
    assert restorer.review(target, _source(snapshot)).links_change

    cluster.topology_intent = _intent(control_domain="control.example.com")
    review = restorer.review(target, _source(snapshot))

    assert not review.links_change
    assert review.dns_names == ("control.example.com",)


def test_a_failed_apply_resumes_without_loading_the_state_again(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)
    review = restorer.review(target, _source(snapshot))
    new_server.fail_apply = True

    with pytest.raises(BackupError, match="apply failed"):
        restorer.run(review, new_server.apply)
    assert ClusterConfig.load().control_restore.step == "installed"  # type: ignore[union-attr]

    resumed, target = _restorer(ClusterConfig.load())
    review = resumed.review(target, RestoreSource(snapshot, None))
    outcome = resumed.run(review, new_server.apply)

    assert review.resume_from == "installed"
    assert new_server.uploads == 1 and len(new_server.applies) == 1
    assert outcome.fleet_epoch == 2


def test_a_finished_restore_lets_the_next_one_start_fresh(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)
    restorer.run(restorer.review(target, _source(snapshot)), new_server.apply)

    assert restorer.in_progress is None


def test_an_old_service_that_still_answers_is_refused_without_force(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: True)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)

    with pytest.raises(BackupError, match="still answers"):
        restorer.review(target, _source(snapshot))
    assert restorer.review(target, _source(snapshot), force=True).target.id == "srv-b"


def test_a_snapshot_of_another_fleet_is_refused(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    cluster.fleet_id = "0f1e2d3c-4b5a-4968-8776-655443322111"
    restorer, target = _restorer(cluster)

    with pytest.raises(BackupError, match="another fleet"):
        restorer.review(target, _source(snapshot))


def test_an_unfinished_restore_refuses_another_target(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)
    new_server.fail_apply = True
    with pytest.raises(BackupError):
        restorer.run(restorer.review(target, _source(snapshot)), new_server.apply)
    other = ServerEntry(host="198.51.100.14", id="srv-c")

    with pytest.raises(BackupError, match="unfinished"):
        restorer.review(other, RestoreSource(snapshot, None))


def test_the_old_servers_resources_are_forgotten_when_it_does_not_answer(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    forgotten: list[str] = []
    monkeypatch.setattr(
        restore_steps, "forget_server", lambda cluster, retiring, server_ref: forgotten.append(server_ref) or []
    )
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)

    review = restorer.review(target, _source(snapshot))
    restorer.run(review, new_server.apply)

    assert not review.old_reachable and forgotten == ["srv-a"]


# -- loading the database onto the new server ---------------------------------------------------


def test_the_upload_stops_the_service_and_checks_what_arrived(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    database.write_bytes(b"SQLite format 3\x00 restored")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    conn = MockConnection()
    conn.when("sha256sum", stdout=f"{digest}  /var/lib/ctld/control.db\n")

    upload_database(conn, database, digest, fleet_id=FLEET_ID)  # type: ignore[arg-type]

    stop = next(index for index, call in enumerate(conn.calls) if "systemctl stop ctld" in call)
    assert "control.db-wal" in conn.calls[stop]
    path, _data, options = conn.writes[0]
    assert path == "/var/lib/ctld/control.db" and options["mode"] == "600" and options["owner"] == "ctld:ctld"


def test_the_upload_refuses_a_server_running_another_fleet(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    database.write_bytes(b"x")
    conn = MockConnection()
    conn.when("/etc/ctld/config.json", stdout='{"fleet_id": "0f1e2d3c-4b5a-4968-8776-655443322111"}')

    with pytest.raises(BackupError, match="another fleet"):
        upload_database(conn, database, "0" * 64, fleet_id=FLEET_ID)  # type: ignore[arg-type]
    assert conn.writes == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({"rc": 255, "stderr": "Connection reset"}, "Could not read which fleet"),
        ({"stdout": "{not json"}, "unreadable"),
    ],
    ids=["unread", "malformed"],
)
def test_the_upload_refuses_a_server_whose_fleet_it_cannot_tell(
    tmp_path: Path, result: dict[str, Any], message: str
) -> None:
    database = tmp_path / "control.db"
    database.write_bytes(b"x")
    conn = MockConnection()
    conn.when("/etc/ctld/config.json", **result)

    with pytest.raises(BackupError, match=message):
        upload_database(conn, database, "0" * 64, fleet_id=FLEET_ID)  # type: ignore[arg-type]
    assert conn.writes == [] and not any("systemctl stop" in call for call in conn.calls)


def test_a_damaged_upload_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    database.write_bytes(b"x")
    digest = hashlib.sha256(b"x").hexdigest()
    conn = MockConnection()
    conn.when("sha256sum", stdout=f"{'0' * 64}  /var/lib/ctld/control.db\n")

    with pytest.raises(BackupError, match="intact"):
        upload_database(conn, database, digest, fleet_id=FLEET_ID)  # type: ignore[arg-type]


# -- the command's local fleet state ------------------------------------------------------------


def test_an_empty_context_takes_the_snapshots_own_fleet_files(fleet: tuple[ClusterConfig, Path]) -> None:
    from meridian.commands.restore import _fleet_state

    cluster, snapshot = fleet
    source = _source(snapshot)
    config.CLUSTER_CONFIG.unlink()
    config.SERVER_PROFILES_FILE.unlink()

    restored = _fleet_state(ClusterConfig(), source.snapshot)

    assert restored.fleet_id == FLEET_ID and restored.topology_intent == cluster.topology_intent
    assert restored.panel.api_token == ""
    assert ServerRegistry(config.SERVER_PROFILES_FILE).find("srv-a") is not None


def test_a_copy_behind_the_snapshots_is_sent_to_a_new_context(fleet: tuple[ClusterConfig, Path]) -> None:
    import typer

    from meridian.commands.restore import _fleet_state

    cluster, snapshot = fleet
    cluster.active_generation = 1

    with pytest.raises(typer.Exit):
        _fleet_state(cluster, _source(snapshot).snapshot)


def test_restore_opens_the_control_services_ports_before_its_bootstrap() -> None:
    from meridian.compiler import compile_topology

    plan = compile_topology(
        single_server_intent(None, server_ref="srv-new", address="198.51.100.30", control="meridian")
    )
    conn = MockConnection()

    restore_steps.open_control_ports(plan, ServerEntry(host="198.51.100.30", id="srv-new"), conn)

    opened = " ".join(call for call in conn.calls if "ufw" in call)
    assert "allow 80/tcp" in opened and "allow 443/tcp" in opened
    # A fresh Debian server has no ufw yet: it is installed before the first rule.
    install = next(index for index, call in enumerate(conn.calls) if "apt-get install" in call and "ufw" in call)
    first_rule = next(index for index, call in enumerate(conn.calls) if call.startswith("ufw allow"))
    assert install < first_rule


def test_a_failed_save_after_the_upload_keeps_the_staged_database_for_the_rerun(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    target = registry.find("srv-b")
    assert target is not None

    def persist(state: ClusterConfig) -> None:
        if state.control_restore is not None and state.control_restore.step == "loaded":
            raise OSError("disk full")
        state.save()

    restorer = ControlRestorer(cluster, registry, persist=persist, connection_builder=lambda entry: MockConnection())
    with pytest.raises(OSError, match="disk full"):
        restorer.run(restorer.review(target, _source(snapshot)), new_server.apply)

    resumed, target = _restorer(ClusterConfig.load())
    outcome = resumed.run(resumed.review(target, RestoreSource(snapshot, None)), new_server.apply)

    assert new_server.uploads == 2 and outcome.fleet_epoch == 2


def test_a_target_another_context_holds_is_refused_at_review(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from meridian.core.errors import LocalStateError

    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)

    def claimed(hosts: Any) -> None:
        if NEW in set(hosts):
            raise LocalStateError(f"{NEW} already belongs to the fleet in context 'other'", category="user")

    monkeypatch.setattr(restore_steps, "refuse_servers_claimed_elsewhere", claimed)
    cluster, snapshot = fleet
    restorer, target = _restorer(cluster)

    with pytest.raises(LocalStateError, match="context 'other'"):
        restorer.review(target, _source(snapshot))
    assert new_server.uploads == 0 and ClusterConfig.load().control_restore is None


def test_an_older_snapshot_restores_above_the_newest_epoch_this_machine_has_seen(
    fleet: tuple[ClusterConfig, Path], new_server: NewServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agents keep the highest epoch they accepted; a restore below it would never be trusted."""
    from meridian.cluster import ControlRestore

    monkeypatch.setattr(restore_steps, "service_answers", lambda url, token: False)
    cluster, snapshot = fleet  # taken at epoch 1
    cluster.control_restore = ControlRestore(step="done", fleet_epoch=3, to_server_ref="srv-a")
    restorer, target = _restorer(cluster)

    review = restorer.review(target, _source(snapshot))
    outcome = restorer.run(review, new_server.apply)

    assert review.next_epoch == 4 and outcome.fleet_epoch == 4
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        assert client.health().fleet_epoch == 4


def test_an_empty_context_drops_an_unfinished_restore_an_older_snapshot_carried(
    fleet: tuple[ClusterConfig, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from meridian.cluster import ControlRestore
    from meridian.cluster_persistence import dump_cluster
    from meridian.commands import restore as restore_command
    from meridian.control.models import DeployerState

    cluster, snapshot = fleet
    source = _source(snapshot)
    carried = cluster.clone()
    carried.panel.api_token = ""
    carried.control_restore = ControlRestore(step="loaded", to_server_ref="srv-gone", fleet_epoch=2)
    deposit = DeployerState(
        cluster_yml=dump_cluster(carried), servers_json="[]", generation=2, deposited_at="2026-10-29T12:00:00Z"
    )
    monkeypatch.setattr(restore_command, "read_deployer_state", lambda database: deposit)
    config.CLUSTER_CONFIG.unlink()
    config.SERVER_PROFILES_FILE.unlink()

    restored = restore_command._fleet_state(ClusterConfig(), source.snapshot)

    assert restored.control_restore is None
