"""The topology settings write carries a transient membership review precondition."""

from typing import Self

from pydantic import Field, model_validator

from meridian.control.models import ServiceSettings
from meridian.core.agent_types import Sha256Hex


class ServiceSettingsWrite(ServiceSettings):
    profile_title: str = Field(default="Meridian", min_length=1, max_length=100)
    expected_presentation_version: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    adopt_presentation: bool = Field(default=False, exclude_if=lambda value: not value)
    expected_access_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)
    expected_profile_declarations_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_seed(self) -> Self:
        if self.expected_presentation_version is not None and not self.adopt_presentation:
            raise ValueError("Presentation seed version requires a reviewed handover.")
        if (
            not self.adopt_presentation or self.expected_presentation_version is not None
        ) and "profile_title" not in self.model_fields_set:
            raise ValueError("The initial settings must include a profile title.")
        return self
