"""Persistence of topology-owned subscription delivery settings."""

from __future__ import annotations

from typing import cast

from meridian.control.store import Store
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING, SUBSCRIPTION_UPDATE_INTERVAL_HOURS
from meridian.core.topology import ClientRoutingPolicy


def update_interval_hours(store: Store) -> int:
    """Older service databases keep the six-hour default without a schema migration."""
    return int(store.meta("update_interval_hours") or SUBSCRIPTION_UPDATE_INTERVAL_HOURS)


def save_update_interval_hours(store: Store, hours: int) -> None:
    """Save a validated interval inside the caller's settings transaction."""
    store.set_meta("update_interval_hours", str(hours))


def client_routing(store: Store) -> ClientRoutingPolicy:
    """Older service databases keep Russian split routing without a schema migration."""
    return cast(ClientRoutingPolicy, store.meta("client_routing") or DEFAULT_CLIENT_ROUTING)


def save_client_routing(store: Store, policy: ClientRoutingPolicy) -> None:
    """Save a validated policy inside the caller's settings transaction."""
    store.set_meta("client_routing", policy)
