"""Device counts and optional device details returned by client management."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from meridian.core.agent_types import Timestamp
from meridian.core.models import CoreModel

DEVICE_KEY_PATTERN = r"[A-Za-z0-9=-]{10,64}"
DeviceCap = Annotated[int, Field(ge=0, le=100, strict=True)]


def effective_device_limit(cap: int | None, default: int) -> int:
    """Null inherits; zero is an explicit exemption from the device cap."""
    return default if cap is None else cap


class ClientDevice(CoreModel):
    """An ID that can be removed; private mode exposes no metadata."""

    device_key: str = Field(min_length=10, max_length=64, pattern=rf"^{DEVICE_KEY_PATTERN}$")


class ClientDeviceDetails(ClientDevice):
    """Details collected only with full device limits."""

    platform: str | None
    os_version: str | None
    model: str | None
    user_agent: str | None
    first_seen_at: Timestamp | None
    last_seen_at: Timestamp | None


class ClientDevicesResult(CoreModel):
    username: str
    level: str = Field(description="off, private, or full; the service's device privacy level.")
    count: int = Field(ge=0)
    limit: DeviceCap = Field(description="The effective device cap; 0 means unlimited.")
    devices: list[ClientDeviceDetails | ClientDevice] = Field(default_factory=list)
    removed: int = Field(default=0, ge=0, description="Rows removed by this command; zero on a read.")


class DevicesRemoved(CoreModel):
    deleted: int = Field(ge=0)
