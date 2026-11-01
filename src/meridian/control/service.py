"""The control service's state and logic, independent of HTTP.

One SQLite file holds the fleet identity, the CA and signing key
(`enrollment.NodeAuthority`), node specs and the signed documents composed from
them, user records, client endpoints, traffic totals (`traffic.TrafficLedger`),
and scoped API tokens (`tokens.TokenRegistry`).
Every change that alters a node's resources composes a new revision, signs it,
and wakes that node's waiting poll.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid as uuid_module
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from meridian.control.account_lifecycle import enforce_user_schedules, preview_user_schedule
from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.attention import read_attention
from meridian.control.background_health import BackgroundHealth
from meridian.control.backups import BackupLedger
from meridian.control.command_models import CommandRequest, NodeCommandRecord, UserCutOffMutation
from meridian.control.commands import CommandLedger
from meridian.control.connection_pages import ConnectionPages
from meridian.control.cutoff import CutOffLedger
from meridian.control.devices import DeviceLedger, HeaderReader
from meridian.control.enrollment import NodeAuthority
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.languages import LanguageLedger
from meridian.control.models import (
    ClientEndpoint,
    EnrollmentGrant,
    NodeSpec,
    NodeStatus,
    PrivacySettings,
    PrivacyState,
    PrivacyUpdateResult,
    ServiceHealth,
    ServiceSettings,
    UserCreate,
    UserDevices,
    UserImportBatch,
    UserImportResult,
    UserRecord,
    UserUpdate,
)
from meridian.control.node_documents import with_users
from meridian.control.node_health import project_runtime
from meridian.control.notices import NoticeLedger
from meridian.control.owner import OwnerAuth
from meridian.control.period_usage import reset_period
from meridian.control.person_connections import read_connections
from meridian.control.privacy import PrivacyLedger, read_privacy
from meridian.control.revisions import RevisionLedger
from meridian.control.schema import SCHEMA, migrate
from meridian.control.schema import SCHEMA_VERSION as SCHEMA_VERSION
from meridian.control.server_allowances import advance_server_periods, read_server_usage
from meridian.control.service_settings import read_settings, save_settings
from meridian.control.store import Store
from meridian.control.subscription_preview import preview_subscription
from meridian.control.subscription_serving import serve_subscription
from meridian.control.subscriptions import SubscriptionResponse
from meridian.control.times import format_timestamp, utc_now
from meridian.control.tokens import TokenRegistry
from meridian.control.traffic import TrafficLedger
from meridian.control.user_import import import_users
from meridian.control.user_lifecycle import extend_user_expiry, insert_user
from meridian.control.user_updates import update_user
from meridian.control.users import USER_SELECT, user_record
from meridian.core.account_schedule import AccountSchedulePreview
from meridian.core.agent_desired import DesiredNodeState
from meridian.core.agent_protocol import Capability, ObservedStateReport, PollRequest, StatsAck, StatsReport
from meridian.core.agent_signing import DESIRED_STATE_CONTEXT
from meridian.core.agent_types import AGENT_PROTOCOL, NodeStateRef, canonical_sha256
from meridian.core.attention import AttentionList, AttentionThresholds
from meridian.core.client_devices import DevicesRemoved
from meridian.core.clients import ClientConnections
from meridian.core.server_allowances import ServerUsageList
from meridian.core.subscription_preview import SubscriptionPreview, SubscriptionPreviewRequest

MAX_WAIT_SECONDS = 55
# meta key prefix: the last revision a deleted node was signed at.
_RETIRED_REVISION = "retired_revision:"


class ControlService:
    """Thread-safe service state over one SQLite database."""

    def __init__(self, database: Path, *, fleet_id: str, version: str, clock: Callable[[], float] = time.time) -> None:
        self._version = version
        self._clock = clock
        self.background = BackgroundHealth(clock=clock)
        self._store = Store(database)
        self._lock = self._store.lock
        self._changed = self._store.changed
        self._db = self._store.db
        self._store.create(SCHEMA)
        # Users read their traffic from the ledger's table, so it exists before any user query.
        self.traffic = TrafficLedger(self._store)
        self.events = EventLedger(self._store, clock=clock)
        self.privacy_ledger = PrivacyLedger(self._store, clock=clock, events=self.events)
        with self._transaction():
            stored = self._meta("fleet_id")
            if stored and stored != fleet_id:
                raise ServiceError("conflict", "The database belongs to another fleet.", 409)
            self._set_meta("fleet_id", fleet_id)
            self._migrate()
            if not self._meta("fleet_epoch"):
                self._set_meta("fleet_epoch", "1")
            if not self._meta("profile_title"):
                self._set_meta("profile_title", "Meridian")
        self.fleet_id = fleet_id
        self.events.prune()
        self.authority = NodeAuthority(self._store, fleet_id, clock=clock)
        self.commands = CommandLedger(self._store, self.authority, clock=clock, events=self.events)
        self.cutoffs = CutOffLedger(self._store, self.commands)
        self.tokens = TokenRegistry(self._store, clock=clock, events=self.events)
        self.revisions = RevisionLedger(self._store, self.tokens, fleet_id, clock=clock)
        self.backups = BackupLedger(self._store, clock=clock)
        self.owner = OwnerAuth(self._store, clock=clock)
        self.devices = DeviceLedger(self._store, clock=clock)
        self.notices = NoticeLedger(self._store, self.events, read_user=self.get_user, clock=clock)
        self.languages = LanguageLedger(self._store, self.events)
        self.pages = ConnectionPages(self._store, self)
        with self._transaction():
            # Documents signed by an earlier signing certificate are signed again.
            self._compose_all()

    # -- identity -----------------------------------------------------------------

    @property
    def fleet_epoch(self) -> int:
        return self._store.fleet_epoch

    def close(self) -> None:
        self._store.close()

    def snapshot(self) -> bytes:
        """This service's state, encrypted to every registered backup key (`backups.py`)."""
        return self.backups.snapshot(fleet_id=self.fleet_id, fleet_epoch=self.fleet_epoch, version=self._version)

    def health(self) -> ServiceHealth:
        return self.background.health(version=self._version, fleet_id=self.fleet_id, fleet_epoch=self.fleet_epoch)

    def conditions(self, *checks: Callable[[], object]) -> AbstractContextManager[None]:
        """Checks every transaction in the block runs before its write (`Store.conditions`)."""
        return self._store.conditions(*checks)

    def put_settings(self, settings: ServiceSettings) -> ServiceSettings:
        return save_settings(self._store, settings)

    def settings(self) -> ServiceSettings:
        return read_settings(self._store)

    def preview_subscription(self, request: SubscriptionPreviewRequest) -> SubscriptionPreview:
        return preview_subscription(
            self._store,
            request,
            read_user=self.get_user,
            read_settings=self.settings,
            read_notice=self.notices.render,
            read_language=self.languages.read,
            read_privacy=self.privacy,
            clock=self._clock,
        )

    def preview_user_schedule(self, request: UserSchedulePreviewRequest) -> AccountSchedulePreview:
        return preview_user_schedule(self._store, request, now=format_timestamp(self._clock()), read_user=self.get_user)

    def server_usage(self, *, now: str | None = None) -> ServerUsageList:
        return read_server_usage(self._store, now or utc_now())

    def privacy(self) -> PrivacySettings:
        return read_privacy(self._store)

    def privacy_state(self) -> PrivacyState:
        return self.privacy_ledger.state()

    def set_privacy(self, settings: PrivacySettings, *, author: str) -> PrivacyUpdateResult:
        return self.privacy_ledger.set(settings, author=author)

    # -- nodes ----------------------------------------------------------------------

    def put_node_spec(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        body = spec.model_dump(mode="json")
        with self._transaction():
            self._ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET spec = ?, spec_sha256 = ? WHERE node_id = ?",
                (json.dumps(body), canonical_sha256(body), node_id),
            )
            self._compose(node_id)
        return self.node_status(node_id)

    def node_specs(self) -> list[NodeSpec]:
        """Every stored node spec (the blob mirror reads their artifacts at start)."""
        rows = self._store.rows("SELECT spec FROM nodes WHERE spec IS NOT NULL")
        return [NodeSpec.model_validate_json(row["spec"]) for row in rows]

    def node_spec(self, node_id: str) -> NodeSpec | None:
        row = self._store.row("SELECT spec FROM nodes WHERE node_id = ?", (node_id,))
        return NodeSpec.model_validate_json(row["spec"]) if row is not None and row["spec"] is not None else None

    def delete_node(self, node_id: str) -> None:
        with self._transaction():
            row = self._node(node_id)
            if row is not None and row["revision"]:
                # An agent that keeps its state refuses an older revision, so a re-added node continues from here.
                self._set_meta(_RETIRED_REVISION + node_id, str(row["revision"]))
            self._db.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            self.events.forget_node(node_id)
            self.commands.expire_node(node_id)
            self.authority.forget_node(node_id)

    def grant_enrollment(self, node_id: str) -> EnrollmentGrant:
        with self._transaction():
            self._ensure_node(node_id)
        return self.authority.grant_enrollment(node_id)

    def node_seen(self, node_id: str, agent_version: str) -> None:
        """Record a hello: the agent is alive and runs `agent_version`."""
        with self._transaction():
            self._ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET last_seen_at = ?, agent_version = ? WHERE node_id = ?",
                (utc_now(), agent_version, node_id),
            )

    def node_status(self, node_id: str) -> NodeStatus:
        row = self._node(node_id)
        if row is None:
            raise ServiceError("not_found", f"Node {node_id!r} is unknown.", 404)
        report = json.loads(row["report"]) if row["report"] else {}
        # The epoch the agent reported, never the service's: a cached report from before a restore
        # names the old epoch, and relabelling it would read as in sync.
        applied_epoch = (report.get("applied") or {}).get("fleet_epoch") or self.fleet_epoch
        applied = (
            NodeStateRef(fleet_epoch=applied_epoch, revision=row["applied_revision"], sha256=row["applied_sha256"])
            if row["applied_revision"]
            else None
        )
        failed: list[str] = []
        errors: list[str] = []
        if report:
            for item in report.get("resources", []):
                if item.get("status") == "failed":
                    failed.append(item["id"])
                    errors.append(f"{item['id']}: {(item.get('error') or {}).get('message', '')}")
        return NodeStatus(
            node_id=node_id,
            fleet_epoch=self.fleet_epoch,
            revision=row["revision"],
            sha256=row["doc_sha256"],
            spec_sha256=row["spec_sha256"],
            applied=applied,
            last_seen_at=row["last_seen_at"],
            agent_version=row["agent_version"],
            failed=failed,
            errors=errors,
            runtime=project_runtime(report),
            **self.traffic.node_accounting(node_id).model_dump(),
        )

    def list_nodes(self) -> list[NodeStatus]:
        rows = self._store.rows("SELECT node_id FROM nodes ORDER BY node_id")
        return [self.node_status(str(row["node_id"])) for row in rows]

    def issue_command(self, node_id: str, request: CommandRequest) -> NodeCommandRecord:
        return self.commands.issue(node_id, request)

    def cut_off_user(self, username: str, *, reissue: bool = False) -> UserCutOffMutation:
        return self.cutoffs.change(username, self.reissue_user if reissue else self.delete_user)

    def poll(
        self,
        node_id: str,
        request: PollRequest,
        *,
        max_wait: float = MAX_WAIT_SECONDS,
        recheck: Callable[[], object] | None = None,
        capabilities: tuple[Capability, ...] = (),
    ) -> dict[str, Any]:
        """Wait up to `wait_seconds` (at most `max_wait`) for a document newer than `have`; return the response.

        `recheck` raises when the caller's session no longer holds: a node re-enrolled or
        deleted while its old session waited must not be handed the new document.
        """
        deadline = time.monotonic() + max(0.0, min(request.wait_seconds, MAX_WAIT_SECONDS, max_wait))
        with self._changed:
            self._touch(node_id)
            while True:
                row = self._node(node_id)
                document = row["document"] if row is not None else None
                commands = self.commands.pending(node_id, capabilities) if row is not None else []
                newer = row is not None and document and _differs(request.have, self.fleet_epoch, row)
                if newer or commands:
                    if recheck is not None:
                        recheck()
                    desired: Any = json.loads(document) if newer and document is not None else None
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    desired = None
                    break
                self._changed.wait(remaining)
        return {
            "type": "poll_result",
            "fleet_epoch": self.fleet_epoch,
            "desired": desired,
            "commands": commands,
            "poll_after_seconds": 0,
        }

    def report(self, node_id: str, report: ObservedStateReport, raw: dict[str, Any]) -> dict[str, Any]:
        if report.node_id != node_id or report.fleet_id != self.fleet_id:
            raise ServiceError("invalid_request", "The report names another node or fleet.", 400)
        applied = report.applied
        with self._transaction():
            self._db.execute(
                "UPDATE nodes SET applied_revision = ?, applied_sha256 = ?, last_seen_at = ?, agent_version = ?,"
                " report = ? WHERE node_id = ?",
                (
                    applied.revision if applied else None,
                    applied.sha256 if applied else None,
                    utc_now(),
                    report.versions.agent,
                    json.dumps(raw),
                    node_id,
                ),
            )
            self.commands.release_ready(node_id, applied)
        return {"type": "report_ack", "applied_revision": applied.revision if applied else None}

    # -- users and endpoints -----------------------------------------------------------

    def put_user(self, username: str) -> UserRecord:
        """Create the user if missing (fresh credentials); an existing user keeps them."""
        with self._transaction():
            existing = self.get_user(username)
            if existing is not None:
                return existing
            self._insert_user(UserCreate(username=username))
            self._compose_all()
            return self._require_user(username)

    def create_user(self, create: UserCreate) -> UserRecord:
        """Create a user with fresh credentials and their first settings at once; refuse a name already taken.

        One transaction, so an interrupted "Add person" never leaves an active user without
        the limits or expiry the form asked for.
        """
        with self._transaction():
            if self.get_user(create.username) is not None:
                raise ServiceError("user_exists", f"User {create.username!r} already exists.", 409)
            self._insert_user(create)
            self._enforce(utc_now())
            self._compose_all()
            return self._require_user(create.username)

    def _insert_user(self, create: UserCreate) -> None:
        insert_user(self._store, create, now=utc_now)
        self.events.append("user.created", create.username)

    def import_users(self, batch: UserImportBatch) -> UserImportResult:
        """Keep credentials, limits, schedules, and usage in one idempotent transaction."""
        return import_users(
            self._store,
            batch,
            read_user=self.get_user,
            now=utc_now,
            enforce=self._enforce,
            compose=self._compose_all,
            events=self.events,
        )

    def update_user(self, username: str, update: UserUpdate) -> UserRecord:
        """Apply the operator's change; extending an expiry or raising a limit makes the user active again."""
        return update_user(
            self._store,
            username,
            update,
            read_user=self._require_user,
            events=self.events,
            now=utc_now,
            enforce=self._enforce,
            compose=self._compose_all,
        )

    def reissue_user(self, username: str) -> UserRecord:
        """Replace link credentials and forget devices atomically; old links stop working."""
        with self._transaction():
            user = self._require_user(username)
            self.devices.remove(user.user_id)
            self._db.execute(
                "UPDATE users SET uuid = ?, sub_token = ? WHERE username = ?",
                (str(uuid_module.uuid4()), secrets.token_urlsafe(18), username),
            )
            self.events.append("user.reissued", username)
            self._compose_all()
            return self._require_user(username)

    def extend_user(self, username: str, *, days: int) -> UserRecord:
        """Extend from now or the current expiry, preserving operator disable and enforcing traffic limits."""
        return extend_user_expiry(
            self._store,
            username,
            days=days,
            now=utc_now,
            read_user=self._require_user,
            enforce=self._enforce,
            record_update=self.events.user_updated,
            compose=self._compose_all,
        )

    def reset_user_traffic(self, username: str) -> UserRecord:
        """Start the user's traffic period now; a user limited by it becomes active again."""
        with self._transaction():
            self._reset_traffic(self._require_user(username), utc_now())
            self.events.append("user.traffic_reset", username)
            self._enforce(utc_now())
            self._compose_all()
            return self._require_user(username)

    def enforce(self, now: str | None = None) -> list[str]:
        """Apply expiry, traffic limits, and periodic resets; return who changed status."""
        moment = now or utc_now()
        self.commands.maintain()
        self.traffic.prune_history(moment)
        self.events.observe_nodes(self.list_nodes, moment)
        with self._transaction():
            changed = self._enforce(moment)
            if changed:
                self._compose_all()
            return changed

    def record_stats(self, node_id: str, report: StatsReport, now: str | None = None) -> StatsAck:
        """Count an agent's stats report and enforce limits on it, in one transaction.

        Periods advance before the growth is counted: traffic that arrives after a UTC
        boundary belongs to the new period, and a reset the timer ran afterwards would
        fold it into the offset and erase it.
        """
        moment = now or utc_now()
        with self._transaction():
            # Counters of a user the service never had would only grow the database.
            known = {row[0] for row in self._db.execute("SELECT user_id FROM users")}
            report = report.model_copy(update={"users": [user for user in report.users if user.user_id in known]})
            changed = self._enforce(moment)
            ack = self.traffic.count(node_id, report, now=moment)
            if [*changed, *self._enforce(moment)]:
                self._compose_all()
        return ack

    def get_user(self, username: str) -> UserRecord | None:
        with self._lock:
            row = self._db.execute(f"{USER_SELECT} WHERE u.username = ?", (username,)).fetchone()
        return user_record(row, now=datetime.fromisoformat(utc_now())) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            rows = self._db.execute(f"{USER_SELECT} ORDER BY u.username").fetchall()
        moment = datetime.fromisoformat(utc_now())
        return [user_record(row, now=moment) for row in rows]

    def user_connections(self, username: str) -> ClientConnections:
        return read_connections(self._store, self, username)

    def attention(self, thresholds: AttentionThresholds | None = None, *, now: str | None = None) -> AttentionList:
        return read_attention(
            self._store, self.list_users, self.list_nodes, thresholds or AttentionThresholds(), now or utc_now()
        )

    def user_devices(self, username: str) -> UserDevices:
        with self._lock:
            return self.devices.for_user(self._require_user(username), self.privacy())

    def delete_user_devices(self, username: str, device_key: str | None = None) -> DevicesRemoved:
        with self._transaction():
            return self.devices.remove(self._require_user(username).user_id, device_key)

    def delete_user(self, username: str) -> None:
        with self._transaction():
            user = self.get_user(username)
            if user is not None:
                self.traffic.forget_user(user.user_id)
                self.events.append("user.removed", username)
            self._db.execute("DELETE FROM users WHERE username = ?", (username,))
            self._compose_all()

    def _require_user(self, username: str) -> UserRecord:
        user = self.get_user(username)
        if user is None:
            raise ServiceError("not_found", f"Unknown user {username!r}.", 404)
        return user

    def _enforce(self, now: str) -> list[str]:
        advance_server_periods(self._store, now)
        return enforce_user_schedules(
            self._store,
            self.list_users(),
            now,
            read_user=self._require_user,
            reset_traffic=self._reset_traffic,
            events=self.events,
        )

    def _reset_traffic(self, user: UserRecord, now: str) -> None:
        reset_period(self._store, user, now)

    def put_endpoint(self, endpoint_id: str, endpoint: ClientEndpoint) -> ClientEndpoint:
        with self._transaction():
            self._db.execute(
                "INSERT INTO endpoints VALUES (?, ?) ON CONFLICT(endpoint_id) DO UPDATE SET body = excluded.body",
                (endpoint_id, endpoint.model_dump_json()),
            )
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> ClientEndpoint | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
        return ClientEndpoint.model_validate_json(row[0]) if row else None

    def delete_endpoint(self, endpoint_id: str) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM endpoints WHERE endpoint_id = ?", (endpoint_id,))

    def subscription(
        self,
        sub_token: str,
        suffix: str = "",
        *,
        user_agent: str | None = None,
        accept: str | None = None,
        device_headers: HeaderReader | None = None,
    ) -> SubscriptionResponse:
        return serve_subscription(
            self._store,
            self.devices,
            sub_token,
            suffix,
            read_settings=self.settings,
            read_privacy=self.privacy,
            read_notice=self.notices.render,
            read_language=self.languages.read,
            clock=self._clock,
            render_page=self.pages.render,
            user_agent=user_agent,
            accept=accept,
            device_headers=device_headers,
        )

    # -- composition -------------------------------------------------------------------

    def _compose_all(self) -> None:
        for (node_id,) in self._db.execute("SELECT node_id FROM nodes WHERE spec IS NOT NULL").fetchall():
            self._compose(str(node_id))
        self.cutoffs.finish()

    def _compose(self, node_id: str) -> None:
        """Sign a new revision when the node's resources changed, and wake its poll."""
        row = self._node(node_id)
        if row is None or not row["spec"]:
            return
        spec = NodeSpec.model_validate_json(row["spec"])
        resources = with_users(spec, self.list_users())
        resources_sha = canonical_sha256(resources)
        held = json.loads(row["document"]) if row["document"] else None
        signed = (held["signature"]["key_id"], held["fleet_epoch"]) if held else None
        # A restore bumps the epoch: the same resources are signed again under it.
        if resources_sha == row["doc_sha256"] and signed == (self.authority.service_key_id, self.fleet_epoch):
            return
        revision = (row["revision"] or int(self._meta(_RETIRED_REVISION + node_id) or 0)) + 1
        document: dict[str, Any] = {
            "protocol": AGENT_PROTOCOL,
            "fleet_id": self.fleet_id,
            "node_id": node_id,
            "fleet_epoch": self.fleet_epoch,
            "revision": revision,
            "sha256": resources_sha,
            "issued_at": utc_now(),
            "resources": resources,
        }
        document["signature"] = self.authority.sign(document, DESIRED_STATE_CONTEXT)
        DesiredNodeState.model_validate_json(json.dumps(document))
        self._db.execute(
            "UPDATE nodes SET revision = ?, document = ?, doc_sha256 = ? WHERE node_id = ?",
            (revision, json.dumps(document, ensure_ascii=False), resources_sha, node_id),
        )
        self._changed.notify_all()

    # -- storage helpers -----------------------------------------------------------------

    def _migrate(self) -> None:
        migrate(self._store)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._store.transaction():
            yield

    def _meta(self, key: str) -> str | None:
        return self._store.meta(key)

    def _set_meta(self, key: str, value: str) -> None:
        self._store.set_meta(key, value)

    def _ensure_node(self, node_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO nodes (node_id) VALUES (?)", (node_id,))
        self.events.watch_node(node_id)

    def _touch(self, node_id: str) -> None:
        self._db.execute("UPDATE nodes SET last_seen_at = ? WHERE node_id = ?", (utc_now(), node_id))

    def _node(self, node_id: str) -> sqlite3.Row | None:
        return self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))


def _differs(have: NodeStateRef | None, fleet_epoch: int, row: sqlite3.Row) -> bool:
    if have is None:
        return True
    return (have.fleet_epoch, have.revision, have.sha256) != (fleet_epoch, row["revision"], row["doc_sha256"])
