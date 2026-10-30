"""Read and persist the control service's topology-owned delivery settings."""

from __future__ import annotations

from meridian.control.delivery_settings import (
    client_routing,
    save_client_routing,
    save_update_interval_hours,
    update_interval_hours,
)
from meridian.control.models import ServiceSettings
from meridian.control.page_settings import page_settings, save_page_settings
from meridian.control.server_allowances import save_server_settings, server_settings
from meridian.control.store import Store
from meridian.control.times import utc_now


def save_settings(store: Store, settings: ServiceSettings) -> ServiceSettings:
    with store.transaction():
        store.set_meta("profile_title", settings.profile_title)
        store.set_meta("public_url", settings.public_url)
        save_update_interval_hours(store, settings.update_interval_hours)
        save_client_routing(store, settings.client_routing)
        save_page_settings(store, settings)
        save_server_settings(store, settings.servers, utc_now())
    return settings


def read_settings(store: Store) -> ServiceSettings:
    return ServiceSettings(
        profile_title=store.meta("profile_title") or "Meridian",
        public_url=store.meta("public_url") or "",
        update_interval_hours=update_interval_hours(store),
        client_routing=client_routing(store),
        servers=server_settings(store),
        **page_settings(store).model_dump(),
    )
