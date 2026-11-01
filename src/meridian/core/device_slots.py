"""Subscriber-visible device capacity, with no device identity or metadata."""

from pydantic import Field

from meridian.core.models import CoreModel


class DeviceSlots(CoreModel):
    used: int = Field(ge=0)
    allowed: int = Field(ge=1, le=100)
