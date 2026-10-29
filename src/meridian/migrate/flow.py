"""Review and run `meridian migrate`, one checkpointed step at a time.

Steps, each saved in `ClusterConfig.control_migration.step` once it finishes:

1. ``backed_up`` — PostgreSQL dump, panel `.env`, and `cluster.yml`, on the control server and at home.
2. ``imported`` — the service runs beside the panel, and the intent's users are in it with their credentials.
3. ``cut_over`` — panel stopped, node containers removed, panel-side bindings forgotten, the admin token
   swapped in, and the intent's control backend set to `meridian`.
4. ``applied`` — the reviewed apply converged and every user's link answers from the service, unchanged.
5. ``done`` — the panel stack and its data are gone, and so is panel-only state in `cluster.yml`.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from meridian.cluster import ClusterConfig, ControlMigration, ManagedResourceBinding, MigrationStep
from meridian.compiler.meridian_backend import validate_meridian_backend
from meridian.compiler.models import AgentRuntimePayload, ResourcePlan
from meridian.control.models import UserImportBatch
from meridian.control_artifacts import agent_binary
from meridian.core.errors import LocalStateError
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.migrate import MigrationError
from meridian.migrate.backup import backup_panel, write_snapshot
from meridian.migrate.cutover import (
    drop_panel_state,
    forget,
    node_runtime_bindings,
    panel_bindings,
    remove_node_runtimes,
    remove_panel,
    stop_panel,
)
from meridian.migrate.service_import import import_over_ssh, install_beside_panel
from meridian.migrate.users import DROPPED_PANEL_STATE, CarriedUsers, carry_users
from meridian.reconciler.fence import local_fence_view
from meridian.reconciler.resource_executor import refuse_unresolved_pending_plan
from meridian.reconciler.resources import PendingPlanConflictError
from meridian.remnawave import MeridianPanel
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.control_service import verify_control_service
from meridian.setup.runtime import SetupReview, SetupRuntime
from meridian.ssh import ServerConnection
from meridian.state_fence import SSHApplyFence, apply_holder

SNAPSHOT_NAME = "users.json"
LINKS_NAME = "links.json"
PanelFactory = Callable[[str, str], MeridianPanel]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]
# Applies the flipped intent through the reviewed apply path, continuing the migration's lease
# (the third argument is its holder); raises (or exits) unless it converged.
ApplyIntent = Callable[[ClusterConfig, SetupIntent, str], None]
Persist = Callable[[ClusterConfig], None]
Renewable = SSHApplyFence | None


@dataclass(frozen=True)
class MigrationReview:
    """What `meridian migrate` will do, shown before anything changes."""

    intent: SetupIntent
    setup: SetupReview
    resume_from: MigrationStep | None
    carried: CarriedUsers | None
    forgotten: list[ManagedResourceBinding]
    node_servers: list[str]
    dropped: tuple[str, ...] = DROPPED_PANEL_STATE
    telegram: bool = False

    @property
    def retired_by_apply(self) -> list[ManagedResourceBinding]:
        """Server-side resources the apply removes once the service serves the fleet."""
        gone = {binding.logical_id for binding in self.forgotten}
        return [
            binding
            for binding in self.setup.retiring
            if binding.logical_id not in gone and binding.resource_kind != "node_runtime"
        ]


@dataclass
class MigrationOutcome:
    subscription_urls: dict[str, str] = field(default_factory=dict)
    backup_dir: str = ""
    server_backup_dir: str = ""
    already_done: bool = False


class ControlMigrator:
    """Move one Remnawave fleet onto the control service in place, resuming from its checkpoint."""

    def __init__(
        self,
        cluster: ClusterConfig,
        registry: ServerRegistry,
        *,
        persist: Persist | None = None,
        panel_factory: PanelFactory = MeridianPanel,
        connection_builder: ConnectionBuilder = connection_for_entry,
        on_step: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cluster = cluster
        self._registry = registry
        self._persist = persist or (lambda state: state.save())
        self._panel_factory = panel_factory
        self._connection_builder = connection_builder
        self._on_step = on_step or (lambda _message: None)
        self._now = clock or (lambda: datetime.now(UTC))
        self._connections: dict[str, ServerConnection] = {}
        self._holder = apply_holder()

    # -- review -----------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        migration = self.cluster.control_migration
        return migration is not None and migration.reached("done")

    def review(self) -> MigrationReview:
        """Refuse what cannot migrate, compile the Meridian backend, and read what will move."""
        intent = self.cluster.topology_intent
        if intent is None:
            raise LocalStateError("This fleet has no reviewed topology.", hint="Deploy it first.", category="user")
        migration = self.cluster.control_migration
        resume = migration.step if migration is not None else None
        if self.cluster.runs_control_service and resume is None:
            raise MigrationError(
                "This fleet already runs Meridian's control service; there is nothing to migrate.",
                category="user",
            )
        flipped = intent.model_copy(update={"control": intent.control.model_copy(update={"backend": "meridian"})})
        validate_meridian_backend(flipped)
        setup = SetupRuntime(self._registry, cluster_loader=lambda: self.cluster).review(
            SetupDraft.from_intent(flipped), cluster=self.cluster
        )
        if migration is None or not migration.reached("cut_over"):
            # What would otherwise refuse the apply only after the panel and its nodes are gone.
            self._refuse_pending_plan(setup.plan)
            self._check_agent_builds(setup.plan)
        carried: CarriedUsers | None = None
        if migration is None or not migration.reached("imported"):
            SSHApplyFence(self._control_connection(flipped)).ensure(local_fence_view(self.cluster))
            carried = self._read_users(flipped)
        return MigrationReview(
            intent=flipped,
            setup=setup,
            resume_from=resume,
            carried=carried,
            forgotten=panel_bindings(self.cluster, setup.plan),
            node_servers=sorted({binding.server_ref for binding in node_runtime_bindings(self.cluster)}),
            telegram=self.cluster.telegram is not None,
        )

    # -- run ------------------------------------------------------------------------------

    def run(self, review: MigrationReview, apply: ApplyIntent) -> MigrationOutcome:
        if self.finished:
            return self.finished_outcome()
        migration = self._start()
        control = self._control_connection(review.intent)
        # Until the cut-over the panel's fence.json guards the fleet: hold its lease through every
        # change and hand it to the apply under the same holder, so no other apply slips between.
        # Once cut over, the apply fences itself on the service.
        fence = None if migration.reached("cut_over") else SSHApplyFence(control, holder=self._holder)
        try:
            if fence is not None:
                self.cluster.fleet_id = fence.acquire(local_fence_view(self.cluster))
            if not migration.reached("backed_up"):
                self._on_step("Backing up the panel database")
                local, remote = backup_panel(control, _stamp(migration.started_at))
                migration.backup_dir, migration.server_backup_dir = str(local), remote
                self._checkpoint(migration, "backed_up", fence)
            if not migration.reached("imported"):
                self._import(migration, review, control, fence)
            elif not migration.reached("cut_over"):
                # The panel kept serving since that import: carry what changed there before it stops.
                self._import(migration, review, control, fence, refresh=True)
            if not migration.reached("cut_over"):
                self._cut_over(migration, review, control, fence)
            if not migration.reached("applied"):
                self._on_step("Applying the Meridian backend")
                apply(self.cluster, review.intent, self._holder)
                urls = self._verify(migration, review)
                self._checkpoint(migration, "applied")
            else:
                urls = {}
        finally:
            if fence is not None:
                fence.release()  # a no-op conflict once the apply has handed the fence to the service
        self._on_step("Removing Remnawave")
        remove_panel(control)
        drop_panel_state(self.cluster)
        migration.finished_at = _iso(self._now())
        self._checkpoint(migration, "done")
        return self._outcome(urls=urls)

    def finished_outcome(self) -> MigrationOutcome:
        return self._outcome(already_done=True)

    def _start(self) -> ControlMigration:
        migration = self.cluster.control_migration
        if migration is None:
            migration = ControlMigration(started_at=_iso(self._now()))
            self.cluster.control_migration = migration
        if not self.cluster.fleet_id:
            self.cluster.fleet_id = str(uuid.uuid4())
        self._persist(self.cluster)
        return migration

    def _import(
        self,
        migration: ControlMigration,
        review: MigrationReview,
        control: ServerConnection,
        fence: Renewable,
        *,
        refresh: bool = False,
    ) -> None:
        if not migration.service_token:
            migration.service_token = secrets.token_urlsafe(32)
            self._persist(self.cluster)
        self._on_step("Installing the control service beside the panel")
        install_beside_panel(control, self.cluster, migration.service_token)
        # Read again right before importing, so the usage carried over is as fresh as it can be.
        carried = self._read_users(review.intent)
        backup = Path(migration.backup_dir)
        write_snapshot(backup, SNAPSHOT_NAME, carried.batch.model_dump_json(indent=2))
        # The links users hold now, from the panel's own address: the apply must keep every one.
        links = {user.username: self.cluster.panel.subscription_url(user.sub_token) for user in carried.batch.users}
        write_snapshot(backup, LINKS_NAME, json.dumps(links, indent=2, sort_keys=True))
        self._on_step(f"{'Refreshing' if refresh else 'Importing'} {len(carried.batch.users)} users")
        batch = UserImportBatch(users=carried.batch.users, replace=refresh)
        result = import_over_ssh(control, batch, migration.service_token)
        migration.imported_users = sorted([*result.created, *result.unchanged, *result.replaced])
        self._checkpoint(migration, "imported", fence)

    def _cut_over(
        self, migration: ControlMigration, review: MigrationReview, control: ServerConnection, fence: Renewable
    ) -> None:
        self._on_step("Stopping the panel")
        stop_panel(control)
        nodes = node_runtime_bindings(self.cluster)
        self._on_step("Removing the panel's node containers")
        remove_node_runtimes(nodes, review.setup.plan, self._server_connections(nodes))
        forget([*panel_bindings(self.cluster, review.setup.plan), *nodes])
        if migration.service_token:
            self.cluster.panel.api_token = migration.service_token
            migration.service_token = ""
        self.cluster.topology_intent = review.intent
        self._checkpoint(migration, "cut_over", fence)

    def _verify(self, migration: ControlMigration, review: MigrationReview) -> dict[str, str]:
        """Every imported user's link answers from the service and is the link they already had."""
        _version, _nodes, urls = verify_control_service(review.setup.plan, self.cluster)
        before = self._links(migration)
        changed = sorted(name for name, url in before.items() if urls.get(name) != url)
        if changed:
            raise MigrationError(
                "These users' subscription links changed: " + ", ".join(changed),
                hint=f"The panel's data is still on the control server, and the backup is in {migration.backup_dir}. "
                "Please report this; do not remove the panel by hand.",
                category="bug",
            )
        return urls

    def _links(self, migration: ControlMigration) -> dict[str, str]:
        path = Path(migration.backup_dir) / LINKS_NAME
        try:
            links = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(links, dict):
                raise ValueError("not a mapping")
            return {str(name): str(url) for name, url in links.items()}
        except (OSError, ValueError) as exc:
            raise MigrationError(
                f"The record of each user's link, {path}, is unreadable: {exc}",
                hint="Restore it from the backup directory, then run `meridian migrate` again.",
            ) from exc

    def _checkpoint(self, migration: ControlMigration, step: MigrationStep, fence: Renewable = None) -> None:
        migration.step = step
        self._persist(self.cluster)
        if fence is not None:
            fence.renew()  # only rewrites once the lease is old enough

    def _outcome(self, *, urls: Mapping[str, str] | None = None, already_done: bool = False) -> MigrationOutcome:
        migration = self.cluster.control_migration
        return MigrationOutcome(
            subscription_urls=dict(urls or {}),
            backup_dir=migration.backup_dir if migration else "",
            server_backup_dir=migration.server_backup_dir if migration else "",
            already_done=already_done,
        )

    # -- reading and connecting ------------------------------------------------------------

    def _refuse_pending_plan(self, plan: ResourcePlan) -> None:
        try:
            refuse_unresolved_pending_plan(plan, self.cluster)
        except PendingPlanConflictError as exc:
            raise MigrationError(
                f"The last apply did not finish: {exc}",
                hint="Run `meridian apply` until it converges, then `meridian migrate`.",
                category="user",
            ) from exc

    def _check_agent_builds(self, plan: ResourcePlan) -> None:
        """Every server that will run an agent has a build of it here, read before anything stops."""
        payloads = [resource.payload for resource in plan.resources]
        servers = sorted({payload.server_ref for payload in payloads if isinstance(payload, AgentRuntimePayload)})
        checked: set[str] = set()
        for server_ref in servers:
            result = self._connection(server_ref).run("uname -m", timeout=30, operation_name="read the architecture")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[:200] or f"exit {result.returncode}"
                raise MigrationError(
                    f"Could not read {server_ref}'s architecture: {detail}",
                    hint="Check the server over SSH, then run `meridian migrate` again.",
                )
            arch = result.stdout.strip()
            if arch not in checked:
                agent_binary(arch)
                checked.add(arch)

    def _read_users(self, intent: SetupIntent) -> CarriedUsers:
        panel = self._panel_factory(self.cluster.panel.url, self.cluster.panel.api_token)
        try:
            users = panel.list_users()
        finally:
            panel.close()
        return carry_users(users, list(intent.access.users))

    def _control_connection(self, intent: SetupIntent) -> ServerConnection:
        return self._connection(intent.control.server_ref)

    def _server_connections(self, bindings: list[ManagedResourceBinding]) -> dict[str, ServerConnection]:
        return {binding.server_ref: self._connection(binding.server_ref) for binding in bindings}

    def _connection(self, server_ref: str) -> ServerConnection:
        if server_ref not in self._connections:
            entry = self._registry.find(server_ref)
            if entry is None:
                raise LocalStateError(
                    f"Saved server {server_ref!r} no longer exists.",
                    hint="Add the server back with its SSH details (`meridian server add`), then migrate.",
                    category="user",
                )
            self._connections[server_ref] = self._connection_builder(entry)
        return self._connections[server_ref]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(started_at: str) -> str:
    """A directory name from the start time: 2026-10-29T12:00:00Z -> 20261029-120000."""
    digits = "".join(character for character in started_at if character.isdigit())
    return f"{digits[:8]}-{digits[8:14]}" if len(digits) >= 14 else "migration"
