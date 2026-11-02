"""Reviewed policy storage and assignment checks; no request or destination data is stored."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from meridian.control.delivery_settings import client_routing
from meridian.control.errors import ServiceError
from meridian.control.store import Store
from meridian.core.delivery_policies import DeliveryPolicySettings, resolve_delivery_policy


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
        require_delivery_policy(settings, profiles)
    data = settings.model_dump(mode="json", include={"delivery_policies", "default_policy", "profile_policies"})
    store.set_meta("delivery_policies", json.dumps(data, ensure_ascii=False, sort_keys=True))


def require_delivery_policy(
    settings: DeliveryPolicySettings, profiles: Sequence[str], person_override: str | None = None
) -> None:
    try:
        resolve_delivery_policy(settings, profiles=profiles, person_override=person_override)
    except ValueError as exc:
        raise ServiceError("invalid_delivery_policy", str(exc), 409) from None
