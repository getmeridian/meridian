"""Review and run `meridian restore --to NEW-SERVER`, one checkpointed step at a time.

Rebuilds a fleet's control service on another server from a snapshot, for when the
control server died or was seized. Steps, each saved in `ClusterConfig.control_restore`
once it finishes (the pattern `meridian migrate` uses):

1. ``staged`` — the snapshot decrypted and staged on this machine: the next epoch, a new fleet CA
   (`control.restore`), and a new root admin token saved in `cluster.yml`.
2. ``loaded`` — the topology names the new server (the control plane and every exit that ran
   beside it), and the staged database sits in the new server's state directory.
3. ``installed`` — the setup path's control bootstrap ran there and the service answers with the
   restored fleet at the new epoch.
4. ``applied`` — the reviewed apply converged: every agent enrolled again under the new CA, endpoints
   and settings name the new address, and the old server's resources retired or were forgotten.
5. ``done`` — connection pages republished; each client's link is in the outcome.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from meridian import __version__, config
from meridian.backup import BackupError
from meridian.backup.pull import saved_snapshots
from meridian.cluster import ClusterConfig, ControlRestore, RestoreStep
from meridian.compiler.deployment import deployment_server_refs
from meridian.compiler.models import ControlServicePayload, FirewallRulePayload, ResourcePlan
from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceError
from meridian.control.restore import stage_restored_database
from meridian.control.snapshot import Snapshot, SnapshotError, SnapshotHeader, read_header
from meridian.core.errors import LocalStateError
from meridian.core.inputs import is_ip
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.fleet_contexts import refuse_servers_claimed_elsewhere
from meridian.handoff import HandoffLinks
from meridian.host_runtime import CONTROL_SERVICE_CONFIG, CONTROL_SERVICE_STATE_DIR, CONTROL_SERVICE_UNIT
from meridian.provision.control_service import SERVICE_USER_COMMAND
from meridian.provision.ensure import ensure_packages, ensure_ufw_rule
from meridian.reconciler.retirement import forget_server
from meridian.reconciler.server_render import render_firewall_rules
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.control_plane import control_payload
from meridian.setup.editor import move_control_server_in_intent
from meridian.setup.runtime import SetupReview, SetupRuntime
from meridian.ssh import ServerConnection

STAGING_DIR = "restore"
REMOTE_DATABASE = f"{CONTROL_SERVICE_STATE_DIR}/control.db"
QUICK_TIMEOUT = 5.0
ApplyIntent = Callable[[ClusterConfig, SetupIntent], None]
Persist = Callable[[ClusterConfig], None]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]


@dataclass(frozen=True)
class RestoreSource:
    """The snapshot to restore from; `snapshot` is None only when resuming past `staged`."""

    path: Path
    snapshot: Snapshot | None


@dataclass(frozen=True)
class RestoreReview:
    """What `meridian restore` will do, shown before anything changes."""

    intent: SetupIntent
    setup: SetupReview
    target: ServerEntry
    from_server_ref: str
    from_address: str
    source: RestoreSource
    resume_from: RestoreStep | None
    links_change: bool
    old_reachable: bool  # the old server's SSH answers, so its resources retire instead of being forgotten
    dns_names: tuple[str, ...]
    moved_exits: tuple[str, ...]
    fleet_epoch: int = 0  # the epoch already staged when resuming; 0 before staging
    epoch_floor: int = 0  # the newest epoch this machine has seen; the restore goes one above it

    @property
    def header(self) -> SnapshotHeader | None:
        return self.source.snapshot.header if self.source.snapshot is not None else None

    @property
    def next_epoch(self) -> int:
        return self.fleet_epoch or self.epoch_floor + 1


@dataclass
class RestoreOutcome:
    links: list[HandoffLinks] = field(default_factory=list)
    links_change: bool = False
    fleet_epoch: int = 0


class ControlRestorer:
    """Rebuild one fleet's control service on another server from a snapshot, resuming from its checkpoint."""

    def __init__(
        self,
        cluster: ClusterConfig,
        registry: ServerRegistry,
        *,
        persist: Persist | None = None,
        connection_builder: ConnectionBuilder = connection_for_entry,
        on_step: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cluster = cluster
        self._registry = registry
        self._persist = persist or (lambda state: state.save())
        self._connection_builder = connection_builder
        self._on_step = on_step or (lambda _message: None)
        self._now = clock or (lambda: datetime.now(UTC))
        self._connections: dict[str, ServerConnection] = {}

    @property
    def in_progress(self) -> ControlRestore | None:
        restore = self.cluster.control_restore
        return restore if restore is not None and restore.step != "done" else None

    # -- review -----------------------------------------------------------------------

    def review(self, target: ServerEntry, source: RestoreSource, *, force: bool = False) -> RestoreReview:
        intent = self.cluster.topology_intent
        if intent is None or not self.cluster.runs_control_service:
            raise BackupError(
                "Only a fleet on Meridian's control service can be restored from a snapshot.", category="user"
            )
        restore = self.in_progress
        if restore is not None and restore.to_server_ref != target.id:
            raise BackupError(
                "A restore onto another server is unfinished.",
                hint=f"Finish it first: meridian restore --to {self._label(restore.to_server_ref)}",
                category="user",
            )
        from_ref = restore.from_server_ref if restore is not None else intent.control.server_ref
        from_entry = self._registry.find(from_ref)
        from_address = restore.from_address if restore is not None else self._address(from_entry)
        if restore is None or not restore.reached("staged"):
            self._check_snapshot(source)
            if not force and service_answers(self.cluster.panel.url, self.cluster.panel.api_token):
                raise BackupError(
                    "The old control service still answers.",
                    hint="Restoring would leave two control services for one fleet. If it is out of your hands "
                    "(seized, or kept by someone else), rerun with --force.",
                    category="user",
                )
        moving = intent.control.server_ref != target.id  # false once a resumed restore saved the move
        if moving and target.id != from_ref and target.id in deployment_server_refs(intent):
            raise BackupError(
                f"{self._label(target.id)} already has a role in this fleet.",
                hint="Restore onto a fresh server, or onto the old control server once it is rebuilt.",
                category="user",
            )
        try:
            updated = move_control_server_in_intent(
                intent, target.id, old_address=from_address, new_address=target.host
            )
        except ValueError as exc:
            raise BackupError(
                f"Cannot move the control plane: {exc}.",
                hint="Remove that relay or gateway first (meridian relay remove / route), then restore.",
                category="user",
            ) from exc
        # The apply refuses these too, but only after the upload and install changed the new server.
        refuse_servers_claimed_elsewhere(
            entry.host
            for entry in (self._registry.find(ref) for ref in deployment_server_refs(updated))
            if entry is not None
        )
        setup = SetupRuntime(self._registry, cluster_loader=lambda: self.cluster).review(
            SetupDraft.from_intent(updated), cluster=self.cluster
        )
        moved = tuple(
            sorted(exit_.id for exit_ in intent.exits if exit_.server_ref == from_ref and from_ref != target.id)
        )
        return RestoreReview(
            intent=updated,
            setup=setup,
            target=target,
            from_server_ref=from_ref,
            from_address=from_address,
            source=source,
            resume_from=restore.step if restore is not None else None,
            links_change=not updated.control.public_hostname and bool(from_address) and from_address != target.host,
            old_reachable=from_ref == target.id or (from_entry is not None and ssh_answers(from_entry)),
            dns_names=_dns_names(updated, target.id),
            moved_exits=moved,
            fleet_epoch=restore.fleet_epoch if restore is not None and restore.reached("staged") else 0,
            epoch_floor=self._highest_known_epoch(source),
        )

    def _highest_known_epoch(self, source: RestoreSource) -> int:
        """The newest fleet epoch this machine has seen: agents refuse any epoch below one they accepted.

        An older snapshot (or one from before an earlier restore) would otherwise restore below
        the epoch the agents hold, and none of them would ever connect again.
        """
        epochs = [source.snapshot.header.fleet_epoch] if source.snapshot is not None else []
        if self.cluster.control_restore is not None:
            epochs.append(self.cluster.control_restore.fleet_epoch)
        for path in saved_snapshots():
            try:
                header = read_header(path.read_bytes())
            except (OSError, SnapshotError):
                continue
            if header.fleet_id == self.cluster.fleet_id:
                epochs.append(header.fleet_epoch)
        return max(epochs, default=0)

    def _check_snapshot(self, source: RestoreSource) -> None:
        if source.snapshot is None:
            raise BackupError("No snapshot to restore from.", category="user")
        header = source.snapshot.header
        if header.fleet_id != self.cluster.fleet_id:
            raise BackupError(
                f"{source.path.name} belongs to another fleet.",
                hint="Pick this fleet's snapshot with --snapshot, or restore into another context (--context).",
                category="user",
            )

    # -- run ------------------------------------------------------------------------------

    def run(self, review: RestoreReview, apply: ApplyIntent) -> RestoreOutcome:
        restore = self._start(review)
        conn = self._connection(review.target)
        target = review.target.name or review.target.host
        if not restore.reached("staged"):
            self._on_step("Preparing the snapshot: next fleet epoch, new fleet CA, new admin token")
            snapshot = review.source.snapshot
            if snapshot is None:
                raise BackupError("No snapshot to restore from.", category="user")
            path = config.FLEET_HOME / STAGING_DIR / "control.db"
            staged = stage_restored_database(
                snapshot.database,
                path,
                fleet_id=self.cluster.fleet_id,
                version=__version__,
                minimum_epoch=review.epoch_floor,
            )
            restore.fleet_epoch, restore.staged_sha256 = staged.fleet_epoch, staged.sha256
            restore.staged_database = str(path)
            # The old token may sit on a seized machine; the restored service accepts only a new one.
            self.cluster.panel.api_token = secrets.token_urlsafe(32)
            self._checkpoint(restore, "staged")
        if not restore.reached("loaded"):
            self._repoint(review)
            self._on_step(f"Loading the restored state onto {target}")
            staged_path = Path(restore.staged_database)
            upload_database(conn, staged_path, restore.staged_sha256, fleet_id=self.cluster.fleet_id)
            restore.staged_database = ""
            # Saved before the file goes: a failed save reruns the upload, which needs it.
            self._checkpoint(restore, "loaded")
            staged_path.unlink(missing_ok=True)
        if not restore.reached("installed"):
            self._on_step(f"Installing the control service on {target}")
            install_service(review.setup.plan, self.cluster, review.target, conn)
            verify_restored(self.cluster, restore.fleet_epoch)
            self._checkpoint(restore, "installed")
        if not restore.reached("applied"):
            self._on_step("Applying the fleet: every agent enrolls with the restored service")
            apply(self.cluster, review.intent)
            self._checkpoint(restore, "applied")
        self._on_step("Publishing connection pages")
        links = publish_pages(self.cluster, list(review.intent.access.users))
        restore.finished_at = _iso(self._now())
        self._checkpoint(restore, "done")
        return RestoreOutcome(links=links, links_change=restore.links_change, fleet_epoch=restore.fleet_epoch)

    def _start(self, review: RestoreReview) -> ControlRestore:
        restore = self.in_progress
        if restore is None:
            restore = ControlRestore(
                started_at=_iso(self._now()),
                snapshot=str(review.source.path),
                from_server_ref=review.from_server_ref,
                from_address=review.from_address,
                to_server_ref=review.target.id,
                links_change=review.links_change,
            )
            self.cluster.control_restore = restore
            self._persist(self.cluster)
        return restore

    def _repoint(self, review: RestoreReview) -> None:
        """Save the topology and control coordinates on the new server; forget what the old one cannot retire."""
        cluster = self.cluster
        cluster.topology_intent = review.intent
        cluster.panel.server_ip = review.target.host
        cluster.panel.ssh_user = review.target.user
        cluster.panel.ssh_port = review.target.port
        # Pages lived on the old control server; they are published again once the fleet is up.
        cluster.connection_page_markers = []
        if not review.old_reachable:
            forget_server(cluster, review.setup.retiring, review.from_server_ref)
        self._persist(cluster)

    def _checkpoint(self, restore: ControlRestore, step: RestoreStep) -> None:
        restore.step = step
        self._persist(self.cluster)

    # -- reading and connecting ------------------------------------------------------------

    def _address(self, entry: ServerEntry | None) -> str:
        return entry.host if entry is not None else self.cluster.panel.server_ip

    def _label(self, server_ref: str) -> str:
        entry = self._registry.find(server_ref)
        return (entry.name or entry.host) if entry is not None else server_ref

    def _connection(self, entry: ServerEntry) -> ServerConnection:
        if entry.id not in self._connections:
            self._connections[entry.id] = self._connection_builder(entry)
        return self._connections[entry.id]


# -- steps (module names, so tests replace them) ------------------------------------------


def service_answers(url: str, token: str) -> bool:
    """Whether a control service answers at `url` at all: a refused token still answers."""
    if not url or not token:
        return False
    try:
        with ControlClient(url, token, timeout=QUICK_TIMEOUT, report_mutations=False) as client:
            client.health()
    except ControlServiceAuthError:
        return True
    except ControlServiceError:
        return False
    return True


def ssh_answers(entry: ServerEntry) -> bool:
    from meridian.health import tcp_connect

    return tcp_connect(entry.host, entry.ssh_port or 22, 10)


def upload_database(conn: ServerConnection, path: Path, sha256: str, *, fleet_id: str) -> None:
    """Put the staged database where the service reads it, with the service stopped; refuse another fleet's server."""
    config_path = shlex.quote(CONTROL_SERVICE_CONFIG)
    read = conn.run(f"if [ -e {config_path} ]; then cat {config_path}; fi", timeout=15, sensitive=True)
    if read.returncode != 0:
        # Unread is not absent: uploading over a server that runs another fleet would wipe it.
        raise BackupError(
            f"Could not read which fleet the new server runs: {read.stderr.strip()[:200] or f'exit {read.returncode}'}",
            hint="Check SSH to the new server, then run the same `meridian restore` again.",
            retryable=True,
        )
    try:
        held = json.loads(read.stdout).get("fleet_id") if read.stdout.strip() else None
    except (ValueError, AttributeError):
        raise BackupError(
            f"{CONTROL_SERVICE_CONFIG} on the new server is unreadable, so its fleet is unknown.",
            hint="Restore onto a fresh server, or move that file aside if it belongs to no fleet.",
            category="user",
        ) from None
    if held and held != fleet_id:
        raise BackupError(
            "That server already runs another fleet's control service.",
            hint="Restore onto a fresh server; never over another fleet.",
            category="user",
        )
    database = shlex.quote(REMOTE_DATABASE)
    prepared = conn.run(
        f"systemctl stop {CONTROL_SERVICE_UNIT} >/dev/null 2>&1; {SERVICE_USER_COMMAND} && "
        f"rm -f {database}-wal {database}-shm",
        timeout=60,
    )
    if prepared.returncode != 0:
        raise BackupError(f"Could not prepare the service's state directory: {prepared.stderr.strip()[:200]}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise LocalStateError(
            f"The staged database {path} changed since it was prepared.",
            hint="Delete it and the control_restore entry in cluster.yml, then restore again.",
            category="user",
        )
    written = conn.put_bytes(REMOTE_DATABASE, data, mode="600", owner="ctld:ctld", sensitive=True, timeout=300)
    check = conn.run(f"sha256sum {database}", timeout=60)
    if written.returncode != 0 or check.stdout.split()[:1] != [sha256]:
        raise BackupError(
            "The restored database did not arrive intact on the new server.",
            hint="Run the same `meridian restore` again; it continues from here.",
            retryable=True,
        )


def install_service(plan: ResourcePlan, cluster: ClusterConfig, entry: ServerEntry, conn: ServerConnection) -> None:
    """The setup path's control bootstrap: baseline, nginx, certificate, and the service over the restored state."""
    from meridian.setup.control_service import bootstrap_reviewed_control_service

    payload = control_payload(plan)
    if not isinstance(payload, ControlServicePayload):
        raise BackupError("The reviewed plan has no Meridian control service.", category="bug")
    open_control_ports(plan, entry, conn)
    bootstrap_reviewed_control_service(payload, plan, cluster, entry, conn)


def open_control_ports(plan: ResourcePlan, entry: ServerEntry, conn: ServerConnection) -> None:
    """Open the firewall rules the control service depends on before its bootstrap.

    A deploy applies them first through the plan; restore bootstraps before the plan runs,
    so a fresh server's SSH-only UFW kept port 80 closed and the ACME challenge timed out.
    """
    # Stock Debian has no ufw, and the baseline that installs it runs later, inside the bootstrap.
    installed = ensure_packages(conn, ["ufw"], timeout=120)
    if not installed.ok:
        raise BackupError(
            f"ufw could not be installed on the new control server: {installed.detail}",
            hint="Check apt on the new server, then run the same `meridian restore` again.",
            retryable=True,
        )
    control = next(item for item in plan.resources if isinstance(item.payload, ControlServicePayload))
    dependencies = set(control.dependencies)
    for resource in plan.resources:
        rule = resource.payload
        if resource.logical_id not in dependencies or not isinstance(rule, FirewallRulePayload):
            continue
        for text in render_firewall_rules(resource.logical_id, rule, {entry.id: entry.host}):
            result = ensure_ufw_rule(conn, text)
            if not result.ok:
                raise BackupError(
                    f"Could not open {rule.port}/{rule.transport} on the new control server: {result.detail}",
                    hint="Check UFW on the new server, then run the same `meridian restore` again.",
                    retryable=True,
                )


def verify_restored(cluster: ClusterConfig, fleet_epoch: int) -> None:
    """The service on the new server must be this fleet at the restored epoch, not a fresh database."""
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token, report_mutations=False) as client:
            health = client.health()
    except ControlServiceError as exc:
        raise BackupError(f"The restored control service does not answer: {exc}", hint=exc.hint) from exc
    if health.fleet_id != cluster.fleet_id or health.fleet_epoch != fleet_epoch:
        raise BackupError(
            f"The new control service runs epoch {health.fleet_epoch}, not the restored {fleet_epoch}.",
            hint="The restored database did not load; please report this.",
            category="bug",
        )


def publish_pages(cluster: ClusterConfig, usernames: Sequence[str]) -> list[HandoffLinks]:
    from meridian.handoff import publish_connection_pages

    return publish_connection_pages(cluster, usernames)


def _dns_names(intent: SetupIntent, server_ref: str) -> tuple[str, ...]:
    """Names the operator must point at the new server: the control domain and its exits' domains."""
    names = {intent.control.public_hostname}
    for exit_ in intent.exits:
        if exit_.server_ref == server_ref:
            names.update(value for path in exit_.paths for value in (path.host, path.tls_sni) if value)
    return tuple(sorted(name for name in names if name and not is_ip(name)))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
