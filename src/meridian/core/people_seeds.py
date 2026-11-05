"""Typed people seeds and resumable ownership handover state."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from meridian.core.client_limits import UserCreationSettings
from meridian.core.inputs import AccessUsernameValue, DisplayNameValue
from meridian.core.models import CoreModel


class PeopleSeeds(CoreModel):
    """Creation seeds outside the topology; existing service accounts keep their settings."""

    users: list[AccessUsernameValue] = Field(default_factory=list)
    display_names: dict[AccessUsernameValue, DisplayNameValue] = Field(
        default_factory=dict, exclude_if=lambda names: not names
    )
    creation_settings: dict[AccessUsernameValue, UserCreationSettings] = Field(
        default_factory=dict,
        exclude_if=lambda settings: not settings,
        description="First account settings only; reviewed applies never replace an existing person's settings.",
    )

    @field_validator("users")
    @classmethod
    def reject_duplicate_users(cls, users: list[str]) -> list[str]:
        if len(users) != len({user.lower() for user in users}):
            raise ValueError("Access usernames must be unique, ignoring case.")
        return users

    @model_validator(mode="after")
    def validate_display_names(self) -> Self:
        unknown = sorted(set(self.display_names) - set(self.users))
        if unknown:
            raise ValueError("Display names refer to unknown users: " + ", ".join(unknown))
        shown = [self.display_name(user).casefold() for user in self.users]
        if len(shown) != len(set(shown)):
            raise ValueError("Each person needs a different name.")
        unknown_settings = sorted(set(self.creation_settings) - set(self.users))
        if unknown_settings:
            raise ValueError("Creation settings refer to unknown users: " + ", ".join(unknown_settings))
        return self

    def display_name(self, username: str) -> str:
        """The name to show for a user: as typed, else the username."""
        return self.display_names.get(username, username)

    def find_user(self, name: str) -> str | None:
        """The username a person means by *name*: a username or a display name, ignoring case."""
        matches = self.matching_users(name)
        return matches[0] if matches else None

    def matching_users(self, name: str) -> list[str]:
        """Every username *name* may mean: an exact username alone, else each case-folded username or display name.

        One person's username can fold onto another's display name, so more than one match is ambiguous.
        """
        if name in self.users:
            return [name]
        wanted = name.strip().casefold()
        return [user for user in self.users if wanted in {user.casefold(), self.display_name(user).casefold()}]


class PeopleAdoption(PeopleSeeds):
    """Legacy declarations transferred only after a reviewed apply commits."""

    state: Literal["pending", "committed"] = "pending"


class PeopleAdoptionNotice(CoreModel):
    people: int = Field(ge=0)
    message: str
