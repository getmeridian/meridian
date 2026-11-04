"""Service person names: stable usernames and casefolded, unambiguous display names."""

from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from meridian.control.errors import ServiceError
from meridian.control.models import UserCreate
from meridian.control.store import Store
from meridian.core.inputs import DisplayNameValue
from meridian.core.topology import assign_access_usernames

_DISPLAY_NAME: TypeAdapter[str] = TypeAdapter(DisplayNameValue)


class PersonNames:
    """One transaction's name holders, including earlier accounts in an import batch."""

    def __init__(self, store: Store) -> None:
        self._holders: dict[str, set[str]] = {}
        self._people: dict[str, set[str]] = {}
        for row in store.rows("SELECT username, display_name FROM users"):
            self.reserve(row["username"], row["display_name"])

    def require_unique(self, username: str, display_name: str, *, excluding: str | None = None) -> None:
        for name in {username.casefold(), display_name.casefold()} - {""}:
            if self._holders.get(name, set()) - {excluding}:
                raise ServiceError("name_conflict", "Each person needs a different name.", 409)

    def reserve(self, username: str, display_name: str) -> None:
        for name in self._people.get(username, set()):
            self._holders[name].discard(username)
        names = {username.casefold(), display_name.casefold()} - {""}
        self._people[username] = names
        for name in names:
            self._holders.setdefault(name, set()).add(username)


def require_unique_person_name(store: Store, username: str, display_name: str, *, excluding: str | None = None) -> None:
    """Refuse shared names inside the caller's write transaction."""
    PersonNames(store).require_unique(username, display_name, excluding=excluding)


def require_unique_display_name(store: Store, username: str, display_name: str) -> None:
    """Refuse a new display name another person holds; the immutable username is not re-checked."""
    PersonNames(store).require_unique("", display_name, excluding=username)


def assign_person_username(store: Store, create: UserCreate) -> UserCreate:
    """Assign the core's Latin username around existing names while holding the write lock."""
    if create.username is not None:
        return create
    reserved = [name for row in store.rows("SELECT username, display_name FROM users") for name in row if name]
    username = assign_access_usernames([create.display_name], reserved=reserved)[0]
    return UserCreate.model_validate({**create.model_dump(exclude_unset=True), "username": username})


def backfill_display_names(store: Store) -> None:
    """Seed names from the latest applied revision, skipping any invalid or already-held name.

    Revisions only checked that names were strings, and console people were never checked
    against topology names, so a bad or clashing legacy name must not stop the service starting.
    """
    row = store.db.execute(
        "SELECT intent FROM revisions WHERE status = 'applied' ORDER BY number DESC LIMIT 1"
    ).fetchone()
    try:
        access = json.loads(row[0]).get("access") if row is not None else None
        names = access.get("display_names") if isinstance(access, dict) else None
    except (TypeError, ValueError, AttributeError):
        names = None
    if not isinstance(names, dict):
        return
    people = PersonNames(store)
    for username, value in sorted(names.items()):
        if not isinstance(username, str) or not isinstance(value, str):
            continue
        try:
            display_name = _DISPLAY_NAME.validate_python(value)
            people.require_unique("", display_name, excluding=username)
        except (ValidationError, ServiceError):
            continue
        if store.db.execute("UPDATE users SET display_name = ? WHERE username = ?", (display_name, username)).rowcount:
            people.reserve(username, display_name)
