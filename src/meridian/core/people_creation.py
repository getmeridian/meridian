"""Service person creation requests shared by CLI, setup drafts and API contracts."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from meridian.core.client_limits import UserCreationSettings
from meridian.core.inputs import USERNAME_PATTERN, DisplayNameValue, validate_display_name_value


class UserCreate(UserCreationSettings):
    """A new user with their first settings, created in one step (the console's "Add person")."""

    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {"required": ["username"], "properties": {"username": {"type": "string"}}},
                {"required": ["display_name"], "properties": {"display_name": {"minLength": 1}}},
            ]
        }
    )
    username: str | None = Field(default=None, pattern=rf"^{USERNAME_PATTERN}$")
    display_name: DisplayNameValue | Literal[""] = Field(
        default="", description="Public person name; the service assigns a Latin username when only this is given."
    )

    @model_validator(mode="after")
    def _name_required(self) -> UserCreate:
        if self.username is None and not self.display_name:
            raise ValueError("A username or display name is required.")
        return self

    @property
    def name(self) -> str:
        return self.display_name or self.username or ""


class UserEnsure(UserCreationSettings):
    """Creation settings for a missing account; existing accounts keep every setting and live name."""

    display_name: DisplayNameValue | Literal[""] = Field(
        default="", description="Fill an empty name atomically; a nonempty service name is always preserved."
    )


def creation_request(name: str, settings: UserCreationSettings | None = None) -> UserCreate:
    """Latin-safe names claim their username; other scripts are assigned by the service."""
    name = validate_display_name_value(name)
    return UserCreate(
        username=name if re.fullmatch(USERNAME_PATTERN, name) else None,
        display_name=name,
        **(settings.to_data() if settings else {}),
    )


def creation_requests(names: Sequence[str], previous: Sequence[UserCreate] = ()) -> list[UserCreate]:
    """Keep a saved request's settings and explicit identity when names are asked again."""
    result = []
    for raw in names:
        name = validate_display_name_value(raw)
        exact = [request for request in previous if request.username == name]
        matches = exact or [
            request
            for request in previous
            if name.casefold() in {request.display_name.casefold(), (request.username or "").casefold()}
        ]
        if len(matches) > 1:
            raise ValueError("A setup name matches more than one creation request.")
        result.append(matches[0] if matches else creation_request(name))
    return result
