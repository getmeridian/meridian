"""Helpers shared by the Remnawave-backed resource drivers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import canonical_hash
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.remnawave import RemnawaveNetworkError, User
from meridian.remnawave.models import is_user_id

if TYPE_CHECKING:
    from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext

ValueT = TypeVar("ValueT")


def resolved_ids(
    context: RemnawaveDriverContext,
    logical_ids: list[str],
    generation: int,
) -> list[str]:
    """Remote IDs of reviewed dependencies; every one must already be bound."""
    values = [context.remote_id(logical_id, generation) for logical_id in logical_ids]
    missing = [logical_id for logical_id, remote_id in zip(logical_ids, values, strict=True) if not remote_id]
    if missing:
        raise ResourceReconcileError(f"Remote identities are missing for reviewed dependencies: {', '.join(missing)}.")
    return values


def one_or_none(values: list[ValueT], description: str) -> ValueT | None:
    if len(values) > 1:
        raise ResourceReconcileError(f"Remnawave contains duplicate {description}.")
    return values[0] if values else None


def observed(
    action: ResourceAction,
    remote_id: str,
    matches: bool,
    projection: object,
) -> ResourceObservation:
    """Observation of an existing remote object, hashed from its projection on mismatch."""
    satisfied = sorted(
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind == "exists"
    )
    return ResourceObservation(
        exists=True,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=satisfied,
    )


def mutation(call: Callable[[], ValueT], description: str) -> ValueT:
    """Run a panel mutation; a network failure leaves its outcome unknown."""
    try:
        return call()
    except RemnawaveNetworkError as exc:
        raise UnknownResourceOutcome(f"Network outcome is unknown while attempting to {description}: {exc}") from exc


def bound_or_named_user(
    context: RemnawaveDriverContext,
    username: str,
    binding: ManagedResourceBinding | None,
) -> User | None:
    if binding is not None and is_user_id(binding.remote_id):
        return context.panel.get_user_by_id(binding.remote_id)
    # Pre-3.x bindings hold the removed user uuid; re-resolve by username.
    return context.panel.get_user(username)
