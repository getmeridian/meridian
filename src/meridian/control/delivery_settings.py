"""Persistence of the topology-owned subscription refresh interval."""

from __future__ import annotations

from meridian.control.store import Store
from meridian.core.defaults import SUBSCRIPTION_UPDATE_INTERVAL_HOURS


def update_interval_hours(store: Store) -> int:
    """Older service databases keep the six-hour default without a schema migration."""
    return int(store.meta("update_interval_hours") or SUBSCRIPTION_UPDATE_INTERVAL_HOURS)


def save_update_interval_hours(store: Store, hours: int) -> None:
    """Save a validated interval inside the caller's settings transaction."""
    store.set_meta("update_interval_hours", str(hours))
