"""Service-owned profile membership and the reviewed policy it names; never observations."""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import TypeAdapter

from meridian.control.errors import ServiceError
from meridian.control.models import ClientEndpoint, UserRecord
from meridian.control.profile_declarations import read_profile_declarations_hash
from meridian.control.store import Store
from meridian.core.access_profiles import DEFAULT_ACCESS_PROFILE, AccessProfileMap, ProfileMembership, may_use_inbound
from meridian.core.agent_types import canonical_sha256

# One self-contained 23 -> 24 step, including defaults and row-version invalidation.
MEMBERSHIP_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS user_access_memberships ("
    "user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE, profile TEXT NOT NULL,"
    " PRIMARY KEY (user_id, profile))",
    "INSERT OR IGNORE INTO user_access_memberships SELECT user_id, 'default' FROM users u"
    " WHERE NOT EXISTS (SELECT 1 FROM user_access_memberships m WHERE m.user_id = u.user_id)",
    "CREATE TRIGGER IF NOT EXISTS access_membership_default AFTER INSERT ON users BEGIN"
    " INSERT INTO user_access_memberships VALUES (NEW.user_id, 'default'); END",
    "CREATE TRIGGER IF NOT EXISTS access_membership_insert AFTER INSERT ON user_access_memberships BEGIN"
    " UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS access_membership_delete AFTER DELETE ON user_access_memberships BEGIN"
    " UPDATE people_versions SET version = version + 1 WHERE user_id = OLD.user_id; END",
)

_POLICY = TypeAdapter(AccessProfileMap)
_MEMBERSHIP = TypeAdapter(ProfileMembership)


def read_access_profiles(store: Store) -> dict[str, list[str]]:
    return _POLICY.validate_json(store.meta("access_profiles") or "{}")


def require_profiles(store: Store, profiles: Sequence[str]) -> list[str]:
    values = _MEMBERSHIP.validate_python(list(profiles))
    unknown = sorted(set(values) - {DEFAULT_ACCESS_PROFILE, *read_access_profiles(store)})
    if unknown:
        raise ServiceError("unknown_profile", "Unknown access profiles: " + ", ".join(unknown) + ".", 400)
    return values


def save_memberships(store: Store, user_id: str, profiles: Sequence[str]) -> None:
    """Validate inside the caller's write and preserve memberships during ordinary applies."""
    values = require_profiles(store, profiles)
    held = [
        row[0]
        for row in store.rows(
            "SELECT profile FROM user_access_memberships WHERE user_id = ? ORDER BY profile", (user_id,)
        )
    ]
    if held == values:
        return
    store.db.execute("DELETE FROM user_access_memberships WHERE user_id = ?", (user_id,))
    store.db.executemany(
        "INSERT INTO user_access_memberships VALUES (?, ?)", [(user_id, profile) for profile in values]
    )


def save_access_profiles(store: Store, profiles: dict[str, list[str]]) -> None:
    """A reviewed profile cannot disappear while any person still names it."""
    values = _POLICY.validate_python(profiles)
    refuse_used_profiles(store, values)
    store.set_meta("access_profiles", json.dumps(values, sort_keys=True))


def refuse_used_profiles(store: Store, values: dict[str, list[str]]) -> None:
    for profile in sorted(set(read_access_profiles(store)) - values.keys()):
        row = store.row("SELECT COUNT(*) FROM user_access_memberships WHERE profile = ?", (profile,))
        assert row is not None
        if row[0]:
            noun = "person" if row[0] == 1 else "people"
            raise ServiceError(
                "profile_in_use",
                f"Access profile {profile} is used by {row[0]} {noun}. Set their profiles before removing it.",
                409,
            )


def profile_access_hash(store: Store) -> str:
    return canonical_sha256(
        {
            **({"declarations_hash": declarations} if (declarations := read_profile_declarations_hash(store)) else {}),
            "profiles": read_access_profiles(store),
            "memberships": [
                tuple(row)
                for row in store.rows(
                    "SELECT u.user_id, u.username, m.profile FROM users u JOIN user_access_memberships m"
                    " ON m.user_id = u.user_id ORDER BY u.username, m.profile"
                )
            ],
        }
    )


def eligible_endpoints(
    user: UserRecord, endpoints: Sequence[ClientEndpoint], profiles: dict[str, list[str]]
) -> list[ClientEndpoint]:
    return [endpoint for endpoint in endpoints if may_use_inbound(user.profiles, profiles, endpoint.inbound_ref)]
