"""Atomic handover of public contact, subscription settings, and server billing to the owner."""

from collections.abc import Callable

from meridian.control.delivery_settings import save_update_interval_hours, update_interval_hours
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import ServiceSettings
from meridian.control.page_settings import page_settings, save_page_settings
from meridian.control.server_allowances import save_server_settings, server_settings
from meridian.control.store import Store
from meridian.core.fleet_events import PresentationFieldsDetail
from meridian.core.presentation import PresentationPatch, PresentationState
from meridian.core.server_allowances import ServerMetadata, ServerMetadataError

VERSION_KEY = "presentation_version"
OWNED_KEY = "presentation_adopted"


def read_presentation(store: Store) -> PresentationState:
    with store.lock:
        return PresentationState(
            version=int(store.meta(VERSION_KEY) or "0"),
            profile_title=store.meta("profile_title") or "Meridian",
            update_interval_hours=update_interval_hours(store),
            owner_contact=page_settings(store).owner_contact,
            servers={ref: spec.metadata for ref, spec in server_settings(store).items()},
        )


def merge_topology_settings(store: Store, settings: ServiceSettings, *, adopt: bool) -> ServiceSettings:
    """Keep operator values; topology still owns which servers exist and which agent measures each one."""
    if store.meta(OWNED_KEY) != "1":
        if adopt:
            store.set_meta(OWNED_KEY, "1")
        return settings
    if not adopt:
        raise ServiceError("presentation_adopted", "Upgrade the Meridian CLI before applying fleet settings.", 409)
    existing = read_presentation(store)
    return settings.model_copy(
        update={
            "profile_title": existing.profile_title,
            "update_interval_hours": existing.update_interval_hours,
            "owner_contact": existing.owner_contact,
            "servers": {
                ref: spec.model_copy(update={"metadata": existing.servers.get(ref, ServerMetadata())})
                for ref, spec in settings.servers.items()
            },
        }
    )


def patch_presentation(
    store: Store, events: EventLedger, patch: PresentationPatch, *, now: Callable[[], str]
) -> PresentationState:
    with store.transaction():
        state = read_presentation(store)
        if patch.expected_version != state.version:
            raise ServiceError("stale_presentation", "Settings changed since review. Reload and review again.", 409)
        try:
            updated = patch.updated(state)
        except KeyError as exc:
            raise ServiceError("unknown_server", "This server is no longer declared in the fleet.", 400) from exc
        except ServerMetadataError as exc:
            raise ServiceError("invalid_request", str(exc), 400) from exc
        changed = [
            field
            for field in ("profile_title", "update_interval_hours", "owner_contact", "servers")
            if getattr(updated, field) != getattr(state, field)
        ]
        if not changed:
            return state
        if "profile_title" in changed:
            store.set_meta("profile_title", updated.profile_title)
        if "update_interval_hours" in changed:
            save_update_interval_hours(store, updated.update_interval_hours)
        if "owner_contact" in changed:
            save_page_settings(store, page_settings(store).model_copy(update={"owner_contact": updated.owner_contact}))
        if "servers" in changed:
            specs = server_settings(store)
            save_server_settings(
                store,
                {ref: spec.model_copy(update={"metadata": updated.servers[ref]}) for ref, spec in specs.items()},
                now(),
            )
        store.set_meta(OWNED_KEY, "1")
        store.set_meta(VERSION_KEY, str(state.version + 1))
        events.append(
            "presentation.changed", "presentation", detail=PresentationFieldsDetail.model_validate({"fields": changed})
        )
        return read_presentation(store)
