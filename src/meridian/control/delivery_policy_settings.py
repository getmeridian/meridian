"""Reviewed policy storage and assignment checks; no request or destination data is stored."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from meridian.control.delivery_settings import client_routing
from meridian.control.errors import ServiceError
from meridian.control.store import Store
from meridian.core.delivery_policies import (
    DeliveryPolicyResolutionError,
    DeliveryPolicySettings,
    ResolvedDeliveryPolicy,
    resolve_delivery_policy,
)

# One self-contained 24 -> 25 step; no columns are added to accounts or node state.
PERSON_DELIVERY_POLICY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS person_delivery_policies ("
    "user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,"
    "policy TEXT NOT NULL CHECK (length(policy) BETWEEN 1 AND 48))",
    "CREATE TRIGGER IF NOT EXISTS person_delivery_policy_insert AFTER INSERT ON person_delivery_policies BEGIN"
    " UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS person_delivery_policy_update AFTER UPDATE ON person_delivery_policies"
    " WHEN OLD.policy != NEW.policy BEGIN"
    " UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS person_delivery_policy_delete AFTER DELETE ON person_delivery_policies BEGIN"
    " UPDATE people_versions SET version = version + 1 WHERE user_id = OLD.user_id; END",
)


def person_delivery_policy(store: Store, user_id: str) -> str | None:
    row = store.row("SELECT policy FROM person_delivery_policies WHERE user_id = ?", (user_id,))
    return str(row[0]) if row is not None else None


def read_delivery_policies(store: Store) -> DeliveryPolicySettings:
    return DeliveryPolicySettings.model_validate(
        {**json.loads(store.meta("delivery_policies") or "{}"), "client_routing": client_routing(store)}
    )


def save_delivery_policies(
    store: Store, settings: DeliveryPolicySettings, *, access_profiles: Mapping[str, Sequence[str]]
) -> None:
    missing = sorted(settings.profile_policies.keys() - {"default", *access_profiles})
    if missing:
        raise ServiceError(
            "unknown_profile", "Delivery defaults name unknown access profiles: " + ", ".join(missing) + ".", 400
        )
    for row in store.rows("SELECT username, user_id FROM users ORDER BY username"):
        profiles = [
            str(item[0])
            for item in store.rows("SELECT profile FROM user_access_memberships WHERE user_id = ?", (row[1],))
        ]
        try:
            require_delivery_policy(settings, profiles, person_delivery_policy(store, str(row[1])))
        except ServiceError as exc:
            # A narrow fleet.apply token must not learn private override names or people.
            if exc.code == "unknown_delivery_policy":
                raise ServiceError(
                    "delivery_policy_in_use",
                    "A person still uses a policy removed from this catalog. "
                    "Clear obsolete overrides before applying it.",
                    409,
                ) from None
            raise ServiceError(
                "conflicting_delivery_policies",
                "Some profile defaults select different policies for the same person. "
                "Set a person override or align the defaults.",
                409,
            ) from None
    data = settings.model_dump(mode="json", include={"delivery_policies", "default_policy", "profile_policies"})
    store.set_meta("delivery_policies", json.dumps(data, ensure_ascii=False, sort_keys=True))


def require_delivery_policy(
    settings: DeliveryPolicySettings, profiles: Sequence[str], person_override: str | None = None
) -> ResolvedDeliveryPolicy:
    try:
        return resolve_delivery_policy(settings, profiles=profiles, person_override=person_override)
    except DeliveryPolicyResolutionError as exc:
        raise ServiceError(exc.code, str(exc), 400 if exc.code == "unknown_delivery_policy" else 409) from None
