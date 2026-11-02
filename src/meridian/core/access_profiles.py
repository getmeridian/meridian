"""Reviewed inbound grants and operator-owned membership, shared by every access surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import AfterValidator, Field, field_validator

from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel

DEFAULT_ACCESS_PROFILE = "default"
AccessReference = Annotated[str, Field(pattern=r"^(?:exit|gateway|inbound|endpoint):[a-z0-9][a-z0-9:_.-]{0,191}$")]
InboundReference = Annotated[str, Field(pattern=r"^inbound:[a-z0-9][a-z0-9:_.-]{0,191}$")]


def default_profiles() -> list[str]:
    return [DEFAULT_ACCESS_PROFILE]


def unique_profiles(profiles: list[str]) -> list[str]:
    if len(profiles) != len(set(profiles)):
        raise ValueError("List each access profile once.")
    return sorted(profiles)


ProfileMembership = Annotated[
    list[TopologyIdValue], Field(min_length=1, max_length=64), AfterValidator(unique_profiles)
]


def canonical_profile_map(profiles: dict[str, list[str]]) -> dict[str, list[str]]:
    if DEFAULT_ACCESS_PROFILE in profiles:
        raise ValueError("The default access profile always grants every managed inbound and cannot be edited.")
    return {name: sorted(set(refs)) for name, refs in sorted(profiles.items())}


AccessProfileMap = Annotated[
    dict[TopologyIdValue, Annotated[list[InboundReference], Field(max_length=10000)]],
    Field(max_length=64),
    AfterValidator(canonical_profile_map),
]


class AccessProfileIntent(CoreModel):
    """A named set of reviewed exits, gateways, public endpoints, or authentication inbounds."""

    id: TopologyIdValue
    grants: list[AccessReference] = Field(max_length=10000)

    @field_validator("id")
    @classmethod
    def reserved_default(cls, value: str) -> str:
        if value == DEFAULT_ACCESS_PROFILE:
            raise ValueError("The default access profile always grants everything and cannot be edited or removed.")
        return value

    @field_validator("grants")
    @classmethod
    def unique_grants(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("List each access grant once.")
        return sorted(values)


def access_reasons(memberships: Sequence[str], profiles: Mapping[str, Sequence[str]], inbound_ref: str) -> list[str]:
    """The memberships granting this authentication inbound; unknown profiles fail closed."""
    return sorted(
        name
        for name in memberships
        if name == DEFAULT_ACCESS_PROFILE or (inbound_ref and inbound_ref in profiles.get(name, ()))
    )


def may_use_inbound(memberships: Sequence[str], profiles: Mapping[str, Sequence[str]], inbound_ref: str) -> bool:
    """The single eligibility rule used for authentication, delivery, and reviews."""
    return bool(access_reasons(memberships, profiles, inbound_ref))
