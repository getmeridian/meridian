"""Authenticated schedule preview requests, without credentials or private metadata."""

from pydantic import Field

from meridian.control.models import USERNAME_PATTERN, UserUpdate
from meridian.core.account_schedule import AccountLifecycleSettings
from meridian.core.agent_types import Timestamp
from meridian.core.client_limits import LimitStrategy


class UserSchedulePreviewRequest(AccountLifecycleSettings):
    username: str | None = Field(
        default=None, pattern=rf"^{USERNAME_PATTERN}$", description="Null previews a new account."
    )
    expire_at: Timestamp | None = None
    traffic_limit_strategy: LimitStrategy | None = None

    @property
    def change(self) -> UserUpdate:
        return UserUpdate.model_validate(self.model_dump(include=self.model_fields_set - {"username"}))
