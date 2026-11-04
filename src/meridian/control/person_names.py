"""Service person names: stable usernames and casefolded, unambiguous display names."""

from __future__ import annotations

from meridian.control.errors import ServiceError
from meridian.control.models import UserCreate
from meridian.control.store import Store
from meridian.core.topology import assign_access_usernames


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


def assign_person_username(store: Store, create: UserCreate) -> UserCreate:
    """Assign the core's Latin username around existing names while holding the write lock."""
    if create.username is not None:
        return create
    reserved = [name for row in store.rows("SELECT username, display_name FROM users") for name in row if name]
    username = assign_access_usernames([create.display_name], reserved=reserved)[0]
    return create.model_copy(update={"username": username})
