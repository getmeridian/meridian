"""Reviewed declaration fingerprints and the settings transaction's profile precondition."""

from __future__ import annotations

from pydantic import TypeAdapter

from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings
from meridian.control.store import Store
from meridian.core.access_profiles import DEFAULT_ACCESS_PROFILE, AccessProfileMap, profile_declarations_hash
from meridian.core.agent_types import Sha256Hex
from meridian.core.models import CoreModel

DECLARATIONS_META = "access_profile_declarations_hash"
_HASH = TypeAdapter(Sha256Hex)
_REVIEW_HINT = (
    "Edit profiles with meridian profile or meridian fleet import, or run meridian apply to review a saved change."
)


class SnapshotProfileAccess(CoreModel):
    """Profile evidence read from a snapshot without opening a service or migrating its database."""

    access_profiles: AccessProfileMap
    declarations_hash: Sha256Hex | None
    has_non_default_memberships: bool


def read_profile_declarations_hash(store: Store) -> str | None:
    value = store.meta(DECLARATIONS_META)
    return _HASH.validate_python(value) if value is not None else None


def has_non_default_memberships(store: Store) -> bool:
    return (
        store.row("SELECT 1 FROM user_access_memberships WHERE profile != ? LIMIT 1", (DEFAULT_ACCESS_PROFILE,))
        is not None
    )


def check_profile_declarations(
    store: Store,
    settings: ServiceSettings,
    *,
    current_profiles: dict[str, list[str]],
    expected_access_hash: str | None,
    expected_profile_declarations_hash: str | None,
) -> None:
    """An unreviewed write keeps declarations; bootstrap requires all evidence to be empty."""
    current = read_profile_declarations_hash(store)
    wanted = settings.access_profile_declarations_hash
    if expected_access_hash is not None:
        return  # The caller checks the membership and declarations hash in this transaction.
    if expected_profile_declarations_hash is not None:
        bootstrap = current is None and not current_profiles and not has_non_default_memberships(store)
        if expected_profile_declarations_hash != (wanted or profile_declarations_hash([])) or (
            current != expected_profile_declarations_hash and not bootstrap
        ):
            raise ServiceError(
                "stale_profile_declarations", "Access profile declarations changed before apply. " + _REVIEW_HINT, 409
            )
    elif (wanted is not None and wanted != current) or (
        current is not None and settings.access_profiles != current_profiles
    ):
        raise ServiceError("profile_review_required", "Access profile changes need review. " + _REVIEW_HINT, 409)


def save_profile_declarations_hash(store: Store, value: str | None) -> None:
    """Missing input preserves history; only a fenced settings write can replace the fingerprint."""
    if value is not None:
        store.set_meta(DECLARATIONS_META, value)
