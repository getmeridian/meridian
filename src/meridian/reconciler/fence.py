"""Apply fencing: refuse to apply from a stale or foreign copy of the fleet state.

Every copy of ``cluster.yml`` (a second laptop, a restored backup, ``deploy local``
on the server) could otherwise rewrite aggregate resources from an old intent and
silently drop what another machine added. The control server keeps one small
record — fleet ID, last committed generation and plan hash, and a lease — and
each apply compares against it. This module holds the pure decision; storage
lives in ``meridian.state_fence``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.core.errors import MeridianError

LEASE_DURATION = timedelta(minutes=30)
RENEW_AFTER = timedelta(minutes=5)


class FleetFenceError(MeridianError):
    """The control server's fence refused this apply."""


@dataclass(frozen=True)
class FenceRecord:
    fleet_id: str
    generation: int = 0
    plan_hash: str = ""
    lease_holder: str = ""
    lease_expires_at: str = ""

    def lease_active(self, now: datetime) -> bool:
        if not self.lease_holder or not self.lease_expires_at:
            return False
        try:
            return datetime.fromisoformat(self.lease_expires_at) > now
        except ValueError:
            return False


@dataclass(frozen=True)
class LocalFenceView:
    """What this copy of ``cluster.yml`` believes."""

    fleet_id: str
    active_generation: int
    active_plan_hash: str
    fresh: bool  # nothing applied yet from this copy


def local_fence_view(cluster: ClusterConfig) -> LocalFenceView:
    return LocalFenceView(
        fleet_id=cluster.fleet_id,
        active_generation=cluster.active_generation,
        active_plan_hash=cluster.active_plan_hash,
        fresh=cluster.active_generation == 0 and not cluster.managed_bindings,
    )


class ApplyFence(Protocol):
    """Taken before the first action, renewed while applying, released on every exit path."""

    def acquire(self, local: LocalFenceView) -> str:
        """Take the lease; return the fleet ID this copy must record."""

    def ensure(self, local: LocalFenceView) -> None:
        """Read-only: raise ``FleetFenceError`` now if ``acquire`` would refuse."""

    def check(self, local: LocalFenceView) -> str:
        """Read-only: why an apply from this copy would be refused, or ``""``."""

    def renew(self) -> None: ...

    def commit(self, generation: int, plan_hash: str) -> None: ...

    def release(self) -> None: ...


def decide_acquire(
    remote: FenceRecord | None,
    local: LocalFenceView,
    *,
    holder: str,
    now: datetime,
    new_fleet_id: str,
    holder_gone: Callable[[str], bool] = lambda _holder: False,
) -> FenceRecord:
    """Return the record to write when this copy may apply, else raise ``FleetFenceError``.

    *holder_gone* says a lease holder is known dead (a crashed run on this machine),
    so its lease need not be waited out.
    """
    expires = (now + LEASE_DURATION).isoformat()
    if remote is None:
        return FenceRecord(
            fleet_id=local.fleet_id or new_fleet_id,
            generation=local.active_generation,
            plan_hash=local.active_plan_hash,
            lease_holder=holder,
            lease_expires_at=expires,
        )
    if local.fleet_id and remote.fleet_id != local.fleet_id:
        if not local.fresh:
            raise FleetFenceError(
                "This control server belongs to a different copy of the fleet.",
                hint=(
                    f"Your {config.HOME_DISPLAY} was not copied from the machine that manages this fleet. "
                    f"Copy {config.HOME_DISPLAY} from that machine, then retry."
                ),
                category="user",
            )
        # A fresh copy may replace a fence left behind by a torn-down fleet.
        remote = FenceRecord(fleet_id=local.fleet_id)
    if remote.lease_active(now) and remote.lease_holder != holder and not holder_gone(remote.lease_holder):
        raise FleetFenceError(
            f"Another apply is running from {remote.lease_holder.split(':')[0]} "
            f"(lease until {remote.lease_expires_at}).",
            hint="Wait for it to finish, then run this again. A crashed apply's lease expires on its own.",
            category="user",
            retryable=True,
        )
    if remote.generation > local.active_generation or (
        remote.generation == local.active_generation
        and remote.generation > 0
        and remote.plan_hash != local.active_plan_hash
    ):
        raise FleetFenceError(
            f"Another machine changed this fleet (generation {remote.generation}; your copy is at "
            f"{local.active_generation}). Applying from this copy would undo its changes.",
            hint=f"Copy {config.HOME_DISPLAY} from the machine that applied last, then retry.",
            category="user",
        )
    return replace(
        remote,
        fleet_id=remote.fleet_id,
        # A copy that is ahead (an earlier commit could not reach the fence) heals it.
        generation=max(remote.generation, local.active_generation),
        plan_hash=local.active_plan_hash if local.active_generation >= remote.generation else remote.plan_hash,
        lease_holder=holder,
        lease_expires_at=expires,
    )


def renewed(record: FenceRecord, *, now: datetime) -> FenceRecord:
    return replace(record, lease_expires_at=(now + LEASE_DURATION).isoformat())


def committed(record: FenceRecord, *, generation: int, plan_hash: str) -> FenceRecord:
    return replace(record, generation=generation, plan_hash=plan_hash, lease_holder="", lease_expires_at="")


def released(record: FenceRecord) -> FenceRecord:
    return replace(record, lease_holder="", lease_expires_at="")
