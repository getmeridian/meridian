"""A secret-free declared-person removal draft bound to one committed topology."""

from typing import Literal

from pydantic import Field, field_validator

from meridian.core.agent_types import FleetId, Sha256Hex
from meridian.core.inputs import AccessUsernameValue
from meridian.core.models import CoreModel


class PeopleRemovalProposal(CoreModel):
    """Remove these declared usernames in one local review and apply; carries no session cut-off choice."""

    schema_name: Literal["meridian.people-removal/v1"] = Field(default="meridian.people-removal/v1", alias="schema")
    fleet_id: FleetId
    revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    plan_hash: Sha256Hex
    intent_sha256: Sha256Hex
    usernames: list[AccessUsernameValue] = Field(min_length=1, max_length=50000)

    @field_validator("usernames")
    @classmethod
    def unique_names(cls, values: list[str]) -> list[str]:
        if len(values) != len({name.casefold() for name in values}):
            raise ValueError("List each declared person once.")
        return sorted(values)
