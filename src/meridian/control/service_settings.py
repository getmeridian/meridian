"""Read and persist the control service's topology-owned delivery settings."""

from __future__ import annotations

from collections.abc import Callable

from meridian.control.access_memberships import profile_access_hash, read_access_profiles, save_access_profiles
from meridian.control.delivery_settings import (
    client_routing,
    save_client_routing,
    save_update_interval_hours,
    update_interval_hours,
)
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings
from meridian.control.page_settings import page_settings, save_page_settings
from meridian.control.server_allowances import save_server_settings, server_settings
from meridian.control.store import Store
from meridian.control.times import utc_now


def save_settings(
    store: Store,
    settings: ServiceSettings,
    *,
    compose: Callable[[], None] | None = None,
    expected_access_hash: str | None = None,
) -> ServiceSettings:
    with store.transaction():
        if expected_access_hash is not None and expected_access_hash != profile_access_hash(store):
            raise ServiceError("stale_access", "Profile membership changed since review. Preview again.", 409)
        save_access_profiles(store, settings.access_profiles)
        store.set_meta("profile_title", settings.profile_title)
        store.set_meta("public_url", settings.public_url)
        save_update_interval_hours(store, settings.update_interval_hours)
        save_client_routing(store, settings.client_routing)
        save_page_settings(store, settings)
        save_server_settings(store, settings.servers, utc_now())
        if compose is not None:
            compose()
    return settings


def read_settings(store: Store) -> ServiceSettings:
    return ServiceSettings(
        access_profiles=read_access_profiles(store),
        profile_title=store.meta("profile_title") or "Meridian",
        public_url=store.meta("public_url") or "",
        update_interval_hours=update_interval_hours(store),
        client_routing=client_routing(store),
        servers=server_settings(store),
        **page_settings(store).model_dump(),
    )
