"""Persist page settings beside the reviewed delivery interval, without a schema migration."""

from __future__ import annotations

from meridian.control.store import Store
from meridian.core.connection_pages import ConnectionPageSettings


def page_settings(store: Store) -> ConnectionPageSettings:
    body = store.meta("connection_page_settings")
    return ConnectionPageSettings.model_validate_json(body) if body else ConnectionPageSettings()


def save_page_settings(store: Store, settings: ConnectionPageSettings) -> None:
    """Save only page fields, inside the caller's settings transaction."""
    page = ConnectionPageSettings.model_validate(settings.model_dump(include=set(ConnectionPageSettings.model_fields)))
    store.set_meta("connection_page_settings", page.model_dump_json())
