"""The topology settings write carries a transient membership review precondition."""

from pydantic import Field

from meridian.control.models import ServiceSettings
from meridian.core.agent_types import Sha256Hex


class ServiceSettingsWrite(ServiceSettings):
    adopt_presentation: bool = Field(default=False, exclude_if=lambda value: not value)
    expected_access_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)
    expected_profile_declarations_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)
