"""Topology-owned connection-page presentation settings."""

from __future__ import annotations

from pydantic import Field

from meridian.core.inputs import OptionalCountryCodeValue, OptionalPaletteValue
from meridian.core.owner_contact import OwnerContactSettings


class ConnectionPageSettings(OwnerContactSettings):
    """Branding is already normalized by the deployer; pages fetch no external images."""

    server_name: str = Field(default="", max_length=100, exclude_if=lambda value: not value)
    server_icon: str = Field(default="", max_length=409_600, exclude_if=lambda value: not value)
    color: OptionalPaletteValue = Field(default="", exclude_if=lambda value: not value)
    exit_countries: list[OptionalCountryCodeValue] = Field(
        default_factory=list, max_length=250, exclude_if=lambda value: not value
    )
