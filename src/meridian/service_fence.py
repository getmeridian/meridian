"""The apply fence of a control-service fleet, kept by the service with its revisions (#95).

Decisions stay in ``meridian.reconciler.fence``; only the storage changes. Once
the control service holds the fence, every apply also records a revision there:
``acquire`` takes the lease (compare-and-swap on the fence's version), then
proposes and approves the reviewed intent; ``commit`` marks the revision applied
and commits the generation; ``release`` without a commit marks it failed.

Bootstrap and migration. The first apply creates the service, so until the
service has answered once, the fence stays in ``fence.json`` exactly as on a
Remnawave fleet, and the revision is recorded at the end if the service is up
by then. The first apply that finds the service answering with no fence adopts
``fence.json``'s state: it marks the file handed over (a lease that never ends,
so an older Meridian refuses) and only then writes the service's fence. From
then on an unreachable service is retried and then refused, never bypassed:
the handed-over file tells this apart from a fleet whose service never came up.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient, ControlServiceConflict, ControlServiceError
from meridian.control.models import (
    ApplyFenceRecord,
    ApplyFenceState,
    RevisionProposal,
    RevisionResult,
)
from meridian.control.revisions import secret_paths
from meridian.core.errors import LocalStateError
from meridian.core.topology import SetupIntent
from meridian.reconciler.fence import (
    RENEW_AFTER,
    FenceRecord,
    FleetFenceError,
    LocalFenceView,
    committed,
    decide_acquire,
    released,
    renewed,
)
from meridian.reconciler.stall_retry import StallNotice, StallRetry, is_transient
from meridian.ssh import ServerConnection
from meridian.state_fence import SSHApplyFence, handed_over, holder_gone, swap_conflict

logger = logging.getLogger(__name__)

T = TypeVar("T")
# A first, short look: a fleet whose service is not up yet must not wait out a long timeout.
QUICK_TIMEOUT = 5.0
TIMEOUT = 20.0
ClientFactory = Callable[[float], ControlClient]


@dataclass(frozen=True)
class RevisionDraft:
    """What one reviewed apply proposes: the secret-free intent and the hashes the operator approved."""

    intent: dict[str, Any]
    plan_hash: str
    review_hash: str

    @classmethod
    def from_intent(cls, intent: SetupIntent, *, plan_hash: str, review_hash: str) -> RevisionDraft:
        body = intent.model_dump(mode="json")
        leaked = secret_paths(body)
        if leaked:
            raise LocalStateError(
                "The topology intent carries secret-shaped fields: " + ", ".join(leaked[:5]),
                hint="This is a Meridian bug; please report it.",
                category="bug",
            )
        return cls(intent=body, plan_hash=plan_hash, review_hash=review_hash)

    def proposal(self) -> RevisionProposal:
        return RevisionProposal(intent=self.intent, plan_hash=self.plan_hash, review_hash=self.review_hash)


@dataclass
class _Located:
    """Where the fence lives now, and what it holds."""

    mode: Literal["service", "ssh"]
    remote: FenceRecord | None
    version: int = 0
    fleet_id: str = ""
    committed_revision: int | None = None
    hand_over: bool = False  # fence.json still holds the fence and must be marked handed over first


@dataclass
class _Run:
    """This apply's state in service mode."""

    record: FenceRecord
    version: int
    committed_revision: int | None
    renewed_at: datetime
    revision: int | None = None
    result_recorded: bool = False


class ControlServiceFence:
    """``ApplyFence`` for a control-service fleet: the service once it answers, ``fence.json`` before."""

    def __init__(
        self,
        conn: ServerConnection,
        cluster: ClusterConfig,
        revision: RevisionDraft | None = None,
        *,
        holder: str | None = None,
        clock: Callable[[], datetime] | None = None,
        on_stall: StallNotice | None = None,
        client_factory: ClientFactory | None = None,
        ssh_fence: SSHApplyFence | None = None,
    ) -> None:
        self._cluster = cluster
        self._draft = revision
        self._ssh = ssh_fence or SSHApplyFence(conn, holder=holder, clock=clock, on_stall=on_stall)
        self._holder = self._ssh.holder
        self._now = clock or (lambda: datetime.now(UTC))
        self._on_stall = on_stall
        self._stall = StallRetry()
        self._client_factory = client_factory or self._open_client
        self._clients: dict[float, ControlClient] = {}
        self._mode: Literal["service", "ssh"] | None = None
        self._run: _Run | None = None
        self._ssh_finished = False

    # -- ApplyFence -------------------------------------------------------------------

    def acquire(self, local: LocalFenceView) -> str:
        try:
            located = self._locate()
            self._mode = located.mode
            if located.mode == "ssh":
                return self._ssh.acquire(local)
            record = self._decide(located, local, new_fleet_id=located.fleet_id or str(uuid.uuid4()))
            if located.hand_over:
                # Older Meridians read fence.json; mark it before the service takes the fence.
                self._ssh.hand_over(record)
            version = self._swap(located.version, record, located.committed_revision)
            self._run = _Run(record, version, located.committed_revision, renewed_at=self._now())
            try:
                self._run.revision = self._propose()
            except BaseException:
                self.release()
                raise
            return record.fleet_id
        finally:
            self._close()

    def ensure(self, local: LocalFenceView) -> None:
        try:
            located = self._locate()
            self._decide(located, local, new_fleet_id="")
        finally:
            self._close()

    def check(self, local: LocalFenceView) -> str:
        try:
            self.ensure(local)
        except FleetFenceError as exc:
            if exc.category != "user":
                return ""  # Unreachable evidence is reported by the observations themselves.
            return f"{exc} {exc.hint}".strip()
        return ""

    def renew(self) -> None:
        if self._mode == "ssh":
            self._ssh.renew()
            return
        run = self._run
        if run is None:
            return
        now = self._now()
        if now - run.renewed_at < RENEW_AFTER:
            return
        try:
            record = renewed(run.record, now=now)
            run.version = self._swap(run.version, record, run.committed_revision)
            run.record, run.renewed_at = record, now
        finally:
            self._close()

    def commit(self, generation: int, plan_hash: str) -> None:
        if self._mode == "ssh":
            self._ssh.commit(generation, plan_hash)
            self._ssh_finished = True
            self._record_late("applied")
            return
        run = self._run
        if run is None:
            return
        try:
            number = run.revision
            if number is not None:
                self._call(lambda client: client.record_revision_result(number, RevisionResult(status="applied")))
                run.result_recorded = True
                run.committed_revision = number
            record = committed(run.record, generation=generation, plan_hash=plan_hash)
            run.version = self._swap(run.version, record, run.committed_revision)
        except FleetFenceError as exc:
            raise FleetFenceError(
                f"Applied and saved locally, but the control service's record was not updated: {exc}",
                hint="Run the same command again; it changes nothing else and records this generation.",
                category="system",
                retryable=True,
            ) from exc
        finally:
            self._close()
        self._run = None

    def release(self) -> None:
        try:
            if self._mode == "ssh":
                if not self._ssh_finished:
                    self._record_late("failed")
                self._ssh.release()
                return
            run, self._run = self._run, None
            if run is None:
                return
            # No retries: a dead service should not hold up the exit, and an unreleased lease expires.
            number = run.revision
            if number is not None and not run.result_recorded:
                result = RevisionResult(status="failed", detail="The apply did not finish.")
                self._quietly(lambda client: client.record_revision_result(number, result))
            try:
                self._swap(run.version, released(run.record), run.committed_revision, patient=False)
            except FleetFenceError:
                pass
        finally:
            self._close()

    # -- where the fence lives ----------------------------------------------------------

    def _locate(self) -> _Located:
        if not (self._cluster.panel.url and self._cluster.panel.api_token):
            stored = self._ssh.read()
            if handed_over(stored):
                raise FleetFenceError(
                    "This fleet's control service keeps its apply fence, and this copy cannot reach it.",
                    hint=f"Copy {config.HOME_DISPLAY} from the machine that manages this fleet, then retry.",
                    category="user",
                )
            return _Located("ssh", stored)
        try:
            state = self._fence_state(QUICK_TIMEOUT, patient=False)
        except FleetFenceError:
            # Whatever answers (nothing yet, a half-finished bootstrap), fence.json says whether to insist.
            stored = self._ssh.read()
            if not handed_over(stored):
                return _Located("ssh", stored)  # the service has never held the fence: it is still being set up
            state = self._fence_state(TIMEOUT, patient=True)
        if state.record is not None:
            remote = _fence_record(state.record)
            return _Located("service", remote, state.version, state.fleet_id, state.record.revision)
        stored = self._ssh.read()
        if handed_over(stored):
            # A move that marked fence.json but never reached the service: adopt the marked state.
            return _Located("service", released(stored) if stored else None, 0, state.fleet_id)
        return _Located("service", stored, 0, state.fleet_id, hand_over=True)

    def _decide(self, located: _Located, local: LocalFenceView, *, new_fleet_id: str) -> FenceRecord:
        return decide_acquire(
            located.remote,
            local,
            holder=self._holder,
            now=self._now(),
            new_fleet_id=new_fleet_id,
            holder_gone=holder_gone,
        )

    def _fence_state(self, timeout: float, *, patient: bool) -> ApplyFenceState:
        try:
            return self._call(lambda client: client.fence(), timeout=timeout, patient=patient)
        except FleetFenceError as exc:
            if not patient or not exc.retryable:
                raise
            raise FleetFenceError(
                "The control service keeps this fleet's apply fence but does not answer.",
                hint="Run `meridian check`, or `systemctl restart ctld` on the control server, then retry.",
                category="system",
                retryable=True,
            ) from exc

    # -- service calls -----------------------------------------------------------------

    def _swap(self, expected: int, record: FenceRecord, revision: int | None, *, patient: bool = True) -> int:
        body = ApplyFenceRecord(
            fleet_id=record.fleet_id,
            generation=record.generation,
            plan_hash=record.plan_hash,
            revision=revision,
            lease_holder=record.lease_holder,
            lease_expires_at=record.lease_expires_at,
        )
        tried = False

        def attempt(client: ControlClient) -> int:
            nonlocal tried
            if tried:
                # The lost attempt may have swapped before its reply was lost.
                current = client.fence()
                if current.version == expected + 1 and current.record == body:
                    return current.version
                if current.version != expected:
                    raise swap_conflict()
            tried = True
            try:
                return client.swap_fence(expected, body).version
            except ControlServiceConflict as exc:
                raise swap_conflict() from exc

        return self._call(attempt, patient=patient)

    def _propose(self) -> int | None:
        if self._draft is None:
            return None
        proposal = self._draft.proposal()
        # A retry after a lost reply may leave one extra revision `proposed`; it is never approved.
        number = self._call(lambda client: client.propose_revision(proposal)).number

        def approve(client: ControlClient) -> None:
            try:
                client.approve_revision(number)
            except ControlServiceConflict:
                current = client.revision(number)
                if current is None or current.status != "approved":
                    raise

        self._call(approve)
        return number

    def _record_late(self, status: Literal["applied", "failed"]) -> None:
        """Record the bootstrap apply once the service it created answers; best effort."""
        draft = self._draft
        if draft is None or not (self._cluster.panel.url and self._cluster.panel.api_token):
            return

        def record(client: ControlClient) -> None:
            number = client.propose_revision(draft.proposal()).number
            client.approve_revision(number)
            client.record_revision_result(number, RevisionResult(status=status))

        self._quietly(record)

    def _quietly(self, fn: Callable[[ControlClient], object]) -> None:
        try:
            self._call(fn, timeout=QUICK_TIMEOUT, patient=False)
        except FleetFenceError as exc:
            logger.info("Could not record the revision on the control service: %s", exc)

    def _call(self, fn: Callable[[ControlClient], T], *, timeout: float = TIMEOUT, patient: bool = True) -> T:
        """Run *fn* against the service; transport loss is retried, and every refusal is a ``FleetFenceError``."""

        def once() -> T:
            try:
                return fn(self._client(timeout))
            except ControlServiceError as exc:
                raise FleetFenceError(
                    str(exc) if is_transient(exc) else f"The control service refused the apply fence: {exc}",
                    hint=exc.hint,
                    category=exc.category,
                    retryable=exc.retryable,
                ) from exc

        return self._stall.call(once, self._on_stall) if patient else once()

    def _client(self, timeout: float) -> ControlClient:
        if timeout not in self._clients:
            self._clients[timeout] = self._client_factory(timeout)
        return self._clients[timeout]

    def _open_client(self, timeout: float) -> ControlClient:
        return ControlClient(self._cluster.panel.url, self._cluster.panel.api_token, timeout=timeout)

    def _close(self) -> None:
        clients, self._clients = self._clients, {}
        for client in clients.values():
            client.close()


def _fence_record(record: ApplyFenceRecord) -> FenceRecord:
    return FenceRecord(
        fleet_id=record.fleet_id,
        generation=record.generation,
        plan_hash=record.plan_hash,
        lease_holder=record.lease_holder,
        lease_expires_at=record.lease_expires_at,
    )
