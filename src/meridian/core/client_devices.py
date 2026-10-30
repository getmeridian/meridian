"""Device counts and optional device details returned by client management."""

from __future__ import annotations

from pydantic import Field

from meridian.core.agent_types import Timestamp
from meridian.core.models import CoreModel

DEVICE_KEY_PATTERN = r"[A-Za-z0-9=-]{10,64}"


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
    limit: int = Field(ge=1, le=100, description="The person's cap, or the fleet default when none is set.")
    devices: list[ClientDeviceDetails | ClientDevice] = Field(default_factory=list)
    removed: int = Field(default=0, ge=0, description="Rows removed by this command; zero on a read.")


class DevicesRemoved(CoreModel):
    deleted: int = Field(ge=0)
