"""`meridian migrate`: refusals, the checkpointed steps in order, resuming after a failure, and the no-op re-run."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import (
    ActionCheckpoint,
    ClusterConfig,
    ManagedResourceBinding,
    PanelConfig,
    RealityKeyBinding,
    TelegramConfig,
    WorkloadBinding,
)
from meridian.compiler import compile_topology
from meridian.control.models import UserImportBatch, UserImportResult
from meridian.core.errors import LocalStateError
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.host_runtime import NODE_DIR
from meridian.migrate import MigrationError
from meridian.migrate.flow import ControlMigrator, MigrationReview
from meridian.reconciler.fence import FleetFenceError
from meridian.remnawave import User
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.control_service import service_base_url as panel_base_url
from meridian.setup.single_server import single_server_intent
from meridian.state_fence import FENCE_FILE
from tests.support.fence_host import FenceHost
from tests.support.mock_connection import MockConnection

CONTROL, RELAY = "198.51.100.12", "198.51.100.21"
SECRET_PATH, SUB_PATH = "a1b2c3d4e5f6a7b8c9d0e1f2", "connect7f3a9b2c"
DUMP = b"PGDMP\x01\x0e\x00fake custom-format dump"
USERS = {
    "alice": ("3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f", "Zq8xT3vNw2LmK7pR"),
    "bob": ("9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a", "hX4_n-Q9rT2wYb7K"),
}

REMNAWAVE_NEVER = "2099-12-31T23:59:59.000Z"


def _intent() -> SetupIntent:
    intent = single_server_intent(None, server_ref="srv-a", address=CONTROL)
    reality = next(path for path in intent.exits[0].paths if path.protocol == "reality")
    relay = TransparentRelayIntent(
        id="relay-a", hop_server_refs=["srv-relay"], exit_ref=intent.exits[0].id, protocol_path_ref=reality.id
    )
    access = intent.access.model_copy(update={"users": ["alice", "bob"]})
    control = intent.control.model_copy(update={"backend": "remnawave"})
    return intent.model_copy(update={"transparent_relays": [relay], "access": access, "control": control})


def _remnawave_fleet(intent: SetupIntent) -> ClusterConfig:
    """What a converged Remnawave apply leaves in cluster.yml."""
    plan = compile_topology(intent)
    bindings = {}
    for resource in plan.resources:
        kind = resource.payload.kind
        bindings[f"{resource.logical_id}@1"] = ManagedResourceBinding(
            logical_id=resource.logical_id,
            resource_kind=kind,
            generation=1,
            remote_id=NODE_DIR if kind == "node_runtime" else f"remote-{len(bindings)}",
            active=True,
            server_ref=str(getattr(resource.payload, "server_ref", "") or ""),
        )
    return ClusterConfig(
        panel=PanelConfig(
            url=panel_base_url(CONTROL, "", SECRET_PATH),
            api_token="remnawave-api-token",
            admin_user="admin",
            admin_pass="admin-password-for-tests",
            server_ip=CONTROL,
            secret_path=SECRET_PATH,
            sub_path=SUB_PATH,
        ),
        telegram=TelegramConfig(bot_token="123456:ABC"),
        topology_intent=intent,
        workloads=[
            WorkloadBinding(
                id=intent.exits[0].id,
                generation=1,
                server_refs=["srv-a"],
                config_profile_uuid="00000000-0000-4000-8000-000000000001",
                reality_keys={"srv-a": RealityKeyBinding(public_key="pbk", private_key="private", short_id="b2b2b2b2")},
            )
        ],
        managed_bindings=bindings,
        image_pins={"remnawave_backend_image": "remnawave/backend:3.4.6@sha256:" + "a" * 64},
        fleet_id="0f1e2d3c-4b5a-4968-8776-655443322110",
        active_generation=1,
        active_plan_hash=plan.plan_hash,
    )


def _panel_users() -> list[User]:
    users = [
        User(
            id=str(index),
            username=name,
            vless_uuid=uuid,
            short_uuid=short,
            status="ACTIVE",
            expire_at=REMNAWAVE_NEVER,
            traffic_limit_strategy="NO_RESET",
            used_traffic_bytes=10 * index,
            lifetime_used_traffic_bytes=100 * index,
        )
        for index, (name, (uuid, short)) in enumerate(USERS.items(), start=1)
    ]
    return [*users, User(id="9", username="meridian_bridge_a", short_uuid="service-short-0001")]


class FakePanel:
    def __init__(self, url: str, token: str) -> None:
        assert token == "remnawave-api-token", "only the panel's own token reads users"

    def list_users(self) -> list[User]:
        return _panel_users()

    def close(self) -> None:
        pass


class ControlHost(MockConnection):
    """Scripted SSH for the control server, with fence.json behind a real compare-and-swap."""

    def __init__(self) -> None:
        super().__init__()
        self.fence = FenceHost()

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> Any:
        if FENCE_FILE in command:
            return self.fence.run(command, timeout)
        return super().run(command, timeout, **kwargs)

    def put_text(self, path: str, text: str, timeout: int = 30, **kwargs: object) -> Any:
        if path.startswith(FENCE_FILE):
            return self.fence.put_text(path, text, timeout, **kwargs)
        return super().put_text(path, text, timeout, **kwargs)

    def lease_holder(self) -> str:
        body = self.fence.files.get(FENCE_FILE)
        return str(json.loads(body)["lease_holder"]) if body else ""


class Fleet:
    """Local state, saved servers, scripted SSH, and fakes for what needs a live service."""

    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch, intent: SetupIntent | None = None) -> None:
        self.registry = ServerRegistry(home / "servers.json")
        self.registry.add(ServerEntry(host=CONTROL, name="control", id="srv-a", auth_state="validated"))
        self.registry.add(ServerEntry(host=RELAY, name="relay", id="srv-relay", auth_state="validated"))
        _remnawave_fleet(intent or _intent()).save()
        self.control = ControlHost()
        self.control.when("pg_dump", stdout=f"{hashlib.sha256(DUMP).hexdigest()}\nbackup-done\n")
        self.control.when("base64 /var/backups/", stdout=base64.encodebytes(DUMP).decode())
        self.control.when("panel.env", stdout="APP_SECRET=secret-for-tests\n")
        self.control.when("uname -m", stdout="x86_64\n")
        self.relay = MockConnection()
        self.installs: list[str] = []
        self.imports: list[UserImportBatch] = []
        self.applied: list[SetupIntent] = []
        self.apply_holders: list[tuple[str, str]] = []
        self.fail_apply = False
        monkeypatch.setattr("meridian.migrate.flow.install_beside_panel", self._install)
        monkeypatch.setattr("meridian.migrate.flow.import_over_ssh", self._import)
        monkeypatch.setattr("meridian.migrate.flow.verify_control_service", self._verify)
        self.agent_archs: list[str] = []
        self.agent_built = True
        monkeypatch.setattr("meridian.migrate.flow.agent_binary", self._agent_binary)
        self.changed_link = ""

    def migrator(self) -> ControlMigrator:
        connections = {"srv-a": self.control, "srv-relay": self.relay}
        return ControlMigrator(
            ClusterConfig.load(),
            self.registry,
            panel_factory=FakePanel,  # type: ignore[arg-type]
            connection_builder=lambda entry: connections[entry.id],  # type: ignore[arg-type,return-value]
        )

    def apply(self, cluster: ClusterConfig, intent: SetupIntent, fence_holder: str) -> None:
        self.applied.append(intent)
        self.apply_holders.append((fence_holder, self.control.lease_holder()))
        if self.fail_apply:
            raise MigrationError("apply failed in the test")
        # The service bootstrap saves its URL where the panel's was, without the trailing slash.
        cluster.panel.url = cluster.panel.url.rstrip("/")
        cluster.save()

    def _agent_binary(self, arch: str) -> None:
        self.agent_archs.append(arch)
        if not self.agent_built:
            raise LocalStateError(f"Meridian's node agent has no build for {arch} yet.", category="user")

    def _install(self, _conn: Any, cluster: ClusterConfig, token: str) -> None:
        assert cluster.panel.api_token == "remnawave-api-token", "the panel keeps its token until the cut-over"
        self.installs.append(token)

    def _import(self, _conn: Any, batch: UserImportBatch, token: str) -> UserImportResult:
        self.imports.append(batch)
        return UserImportResult(created=[user.username for user in batch.users], unchanged=[])

    def _verify(self, _plan: Any, cluster: ClusterConfig) -> tuple[str, int, dict[str, str]]:
        urls = {name: cluster.panel.subscription_url(short) for name, (_uuid, short) in USERS.items()}
        if self.changed_link:
            urls[self.changed_link] += "-changed"
        return "4.0.0", 1, urls


@pytest.fixture
def fleet(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    return Fleet(tmp_home, monkeypatch)


def _run(fleet: Fleet) -> tuple[ControlMigrator, MigrationReview]:
    migrator = fleet.migrator()
    review = migrator.review()
    migrator.run(review, fleet.apply)
    return migrator, review


def test_review_names_who_moves_what_goes_with_the_panel_and_what_stays_behind(fleet: Fleet) -> None:
    review = fleet.migrator().review()

    assert review.carried is not None
    assert [user.username for user in review.carried.batch.users] == ["alice", "bob"]
    assert review.carried.skipped == ["meridian_bridge_a"]
    assert review.node_servers == ["srv-a"]
    assert {binding.resource_kind for binding in review.forgotten} >= {"host", "config_profile", "access_user"}
    assert "control_plane_runtime" in {binding.resource_kind for binding in review.forgotten}
    assert review.telegram
    assert fleet.control.calls and not any("docker stop" in call for call in fleet.control.calls)


def test_a_full_run_backs_up_imports_cuts_over_applies_and_removes_remnawave(fleet: Fleet, tmp_home: Path) -> None:
    _run(fleet)

    cluster = ClusterConfig.load()
    migration = cluster.control_migration
    assert migration is not None and migration.step == "done" and migration.finished_at
    backup = Path(migration.backup_dir)
    assert backup.parent == tmp_home / "backups"
    assert (backup / "panel.dump").read_bytes() == DUMP
    assert {path.name for path in backup.iterdir()} >= {"panel.dump", "panel.env", "cluster.yml", "users.json"}
    assert all(oct(path.stat().st_mode & 0o777) == "0o600" for path in backup.iterdir())
    assert migration.server_backup_dir.startswith("/var/backups/meridian-migration/")
    # The users moved with Remnawave's credentials, and the apply ran the Meridian backend.
    (batch,) = fleet.imports
    assert {(user.username, user.uuid, user.sub_token) for user in batch.users} == {
        (name, uuid, short) for name, (uuid, short) in USERS.items()
    }
    assert [intent.control.backend for intent in fleet.applied] == ["meridian"]
    assert cluster.runs_control_service
    assert cluster.panel.api_token == fleet.installs[0] and migration.service_token == ""


def test_the_order_is_dump_then_stop_then_node_removal_then_apply_then_panel_removal(fleet: Fleet) -> None:
    _run(fleet)
    calls = fleet.control.calls

    def first(pattern: str) -> int:
        return next(index for index, call in enumerate(calls) if pattern in call)

    assert first("pg_dump") < first("docker update --restart=no") < first(f"{NODE_DIR}/docker-compose.yml")
    assert first(f"{NODE_DIR}/docker-compose.yml") < first("rm -rf /opt/ctlsvc")
    assert not any("docker" in call for call in fleet.relay.calls), "a relay runs no panel containers"


def test_a_node_an_unfinished_apply_installed_is_removed_too(fleet: Fleet) -> None:
    cluster = ClusterConfig.load()
    cluster.pending_generation = 2
    cluster.managed_bindings["node-runtime:relay@2"] = ManagedResourceBinding(
        logical_id="node-runtime:relay",
        resource_kind="node_runtime",
        generation=2,
        remote_id=NODE_DIR,
        active=False,
        server_ref="srv-relay",
    )
    cluster.save()

    review = fleet.migrator().review()

    assert review.node_servers == ["srv-a", "srv-relay"]
    fleet.migrator().run(review, fleet.apply)
    assert any(f"{NODE_DIR}/docker-compose.yml" in call for call in fleet.relay.calls)
    assert sum(f"{NODE_DIR}/docker-compose.yml" in call for call in fleet.control.calls) >= 1
    assert "node-runtime:relay@2" not in ClusterConfig.load().managed_bindings


def test_the_migration_holds_the_lease_and_the_apply_continues_it(fleet: Fleet) -> None:
    _run(fleet)

    ((holder, held),) = fleet.apply_holders
    assert holder and held == holder
    assert fleet.control.lease_holder() == "", "released at the end"


def _foreign_lease(fleet: Fleet) -> None:
    until = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    record = {"fleet_id": ClusterConfig.load().fleet_id, "generation": 1, "lease_holder": "elsewhere:0:1:ab"}
    fleet.control.fence.files[FENCE_FILE] = json.dumps(record | {"lease_expires_at": until})


def test_a_resume_before_the_cut_over_carries_what_changed_in_the_panel_since_the_import(fleet: Fleet) -> None:
    fleet.control.when("docker update --restart=no", rc=1, stderr="daemon gone")
    with pytest.raises(MigrationError, match="Could not stop the panel"):
        _run(fleet)
    (first,) = fleet.imports
    assert not first.replace

    migrator = fleet.migrator()
    with pytest.raises(MigrationError, match="Could not stop the panel"):
        migrator.run(migrator.review(), fleet.apply)

    assert [batch.replace for batch in fleet.imports] == [False, True]


def test_a_lease_taken_after_the_review_stops_the_run_before_anything_changes(fleet: Fleet) -> None:
    migrator = fleet.migrator()
    review = migrator.review()
    _foreign_lease(fleet)

    with pytest.raises(FleetFenceError, match="Another apply is running"):
        migrator.run(review, fleet.apply)

    assert not any("pg_dump" in call or "docker" in call for call in fleet.control.calls)


def test_a_resumed_migration_takes_the_lease_again_before_stopping_the_panel(fleet: Fleet) -> None:
    fleet.control.when("docker update --restart=no", rc=1, stderr="daemon gone")
    with pytest.raises(MigrationError, match="Could not stop the panel"):
        _run(fleet)
    assert ClusterConfig.load().control_migration.step == "imported"  # type: ignore[union-attr]
    _foreign_lease(fleet)
    stops = sum("docker update --restart=no" in call for call in fleet.control.calls)

    migrator = fleet.migrator()
    with pytest.raises(FleetFenceError, match="Another apply is running"):
        migrator.run(migrator.review(), fleet.apply)

    assert sum("docker update --restart=no" in call for call in fleet.control.calls) == stops


def test_cut_over_forgets_panel_objects_and_the_end_drops_panel_only_state(fleet: Fleet) -> None:
    _run(fleet)

    cluster = ClusterConfig.load()
    kinds = {binding.resource_kind for binding in cluster.managed_bindings.values()}
    assert not kinds & {"host", "config_profile", "access_user", "control_plane_runtime", "node_runtime"}
    assert (cluster.panel.admin_user, cluster.panel.admin_pass) == ("", "")
    assert cluster.telegram is None and cluster.image_pins == {}
    assert cluster.workloads[0].config_profile_uuid == ""
    assert cluster.workloads[0].reality_keys["srv-a"].public_key == "pbk", "Reality keys stay"
    assert (cluster.panel.secret_path, cluster.panel.sub_path) == (SECRET_PATH, SUB_PATH)


def test_a_failed_apply_resumes_from_the_cut_over_without_repeating_earlier_steps(fleet: Fleet) -> None:
    fleet.fail_apply = True
    migrator = fleet.migrator()
    review = migrator.review()
    with pytest.raises(MigrationError, match="apply failed"):
        migrator.run(review, fleet.apply)
    assert ClusterConfig.load().control_migration.step == "cut_over"  # type: ignore[union-attr]
    dumps = sum("pg_dump" in call for call in fleet.control.calls)

    fleet.fail_apply = False
    resumed = fleet.migrator()
    resumed_review = resumed.review()
    resumed.run(resumed_review, fleet.apply)

    assert resumed_review.resume_from == "cut_over" and resumed_review.carried is None
    assert sum("pg_dump" in call for call in fleet.control.calls) == dumps
    assert len(fleet.imports) == 1 and len(fleet.applied) == 2
    assert ClusterConfig.load().control_migration.step == "done"  # type: ignore[union-attr]


def test_a_finished_migration_is_a_no_op(fleet: Fleet) -> None:
    _run(fleet)
    calls = len(fleet.control.calls)

    again = fleet.migrator()
    outcome = again.finished_outcome() if again.finished else None

    assert outcome is not None and outcome.already_done
    assert len(fleet.control.calls) == calls


def test_a_changed_link_stops_before_the_panel_data_is_deleted(fleet: Fleet) -> None:
    fleet.changed_link = "bob"
    migrator = fleet.migrator()
    review = migrator.review()

    with pytest.raises(MigrationError, match="bob") as error:
        migrator.run(review, fleet.apply)

    assert error.value.category == "bug"
    assert ClusterConfig.load().control_migration.step == "cut_over"  # type: ignore[union-attr]
    assert not any("rm -rf /opt/ctlsvc" in call for call in fleet.control.calls)


def test_links_compared_are_the_ones_users_held_before_the_cut_over(fleet: Fleet) -> None:
    _run(fleet)

    migration = ClusterConfig.load().control_migration
    assert migration is not None
    links = json.loads((Path(migration.backup_dir) / "links.json").read_text())
    assert links["alice"] == f"https://{CONTROL}/{SUB_PATH}/sub/{USERS['alice'][1]}"


def _never_dial(_entry: ServerEntry) -> Any:
    raise AssertionError("nothing is dialed before the refusal")


def test_a_fleet_already_on_the_control_service_is_refused(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet = Fleet(tmp_home, monkeypatch)
    cluster = ClusterConfig.load()
    intent = cluster.topology_intent
    assert intent is not None
    cluster.topology_intent = intent.model_copy(
        update={"control": intent.control.model_copy(update={"backend": "meridian"})}
    )
    cluster.save()

    with pytest.raises(MigrationError, match="already runs") as error:
        ControlMigrator(ClusterConfig.load(), fleet.registry, connection_builder=_never_dial).review()

    assert error.value.category == "user"


def test_client_ip_forwarding_is_dropped_and_named_in_the_review(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = Fleet(tmp_home, monkeypatch, intent=_intent().model_copy(update={"forward_client_ips": True}))

    review = fleet.migrator().review()

    assert review.client_ips
    assert not review.intent.forward_client_ips


def test_an_unresolved_earlier_apply_is_refused_before_anything_stops(fleet: Fleet) -> None:
    cluster = ClusterConfig.load()
    cluster.pending_plan_hash, cluster.pending_generation = "f" * 64, 2
    cluster.action_checkpoints["node@2"] = ActionCheckpoint(
        idempotency_key="node@2", resource_id="node", generation=2, status="unknown"
    )
    cluster.save()

    with pytest.raises(MigrationError, match="last apply did not finish") as error:
        fleet.migrator().review()

    assert "meridian apply" in (error.value.hint or "")
    assert not any("docker" in call for call in fleet.control.calls)


def test_a_server_with_no_agent_build_is_refused_before_anything_stops(fleet: Fleet) -> None:
    fleet.agent_built = False

    with pytest.raises(LocalStateError, match="no build for x86_64"):
        fleet.migrator().review()

    assert not any("docker" in call for call in fleet.control.calls)


def test_a_resumed_cut_over_skips_the_checks_the_apply_itself_settles(fleet: Fleet) -> None:
    fleet.fail_apply = True
    with pytest.raises(MigrationError):
        _run(fleet)
    fleet.agent_built, fleet.fail_apply = False, False

    _run(fleet)

    assert fleet.agent_archs == ["x86_64"]


def _command_migrator(fleet: Fleet, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Route the command's migrator through the test fleet's fakes; record whether it ran."""
    ran: list[str] = []
    connections = {"srv-a": fleet.control, "srv-relay": fleet.relay}

    class Migrator(ControlMigrator):
        def __init__(self, cluster: ClusterConfig, registry: ServerRegistry, **options: Any) -> None:
            options |= {"panel_factory": FakePanel, "connection_builder": lambda entry: connections[entry.id]}
            super().__init__(cluster, registry, **options)

        def run(self, review: MigrationReview, apply: Any) -> Any:
            ran.append("run")
            return super().run(review, fleet.apply)

    monkeypatch.setattr("meridian.commands.migrate.ControlMigrator", Migrator)
    return ran


def test_the_command_stops_after_the_review_without_a_terminal_or_yes(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _command_migrator(fleet, monkeypatch)
    monkeypatch.setattr("meridian.commands.migrate.confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("meridian.commands.migrate.has_tty", lambda: False)

    result = CliRunner().invoke(app, ["migrate"])

    assert result.exit_code == 1 and not ran
    assert "Moves" in result.output and "pass --yes" in result.output
    assert ClusterConfig.load().control_migration is None


def test_the_command_migrates_with_yes_and_a_rerun_says_nothing_to_do(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _command_migrator(fleet, monkeypatch)

    first = CliRunner().invoke(app, ["migrate", "--yes"])
    again = CliRunner().invoke(app, ["migrate", "--yes"])

    assert first.exit_code == 0, first.output
    assert "links are unchanged" in first.output
    assert again.exit_code == 0 and "nothing to do" in again.output and ran == ["run"]
