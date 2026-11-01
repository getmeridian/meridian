"""One typed people query for the service, list command, and reviewed selections."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from meridian.core.account_schedule import AccountLifecycleState, AccountSchedulePreview
from meridian.core.agent_types import Timestamp
from meridian.core.client_devices import DeviceCap
from meridian.core.client_limits import LimitStrategy
from meridian.core.languages import Language
from meridian.core.models import CoreModel
from meridian.core.person_activity import PersonAttentionReason
from meridian.core.person_metadata import PersonTag, validate_person_tag
from meridian.core.traffic import PeriodTraffic, UserTrafficEvidence

PeopleStatus = Literal["", "active", "disabled", "expired", "limited"]
PeopleSort = Literal["name", "usage", "expiry", "activity"]
PeopleLifecycle = Literal["", "pending", "renewing", "fixed", "unlimited"]


class PeopleQuery(CoreModel):
    q: str = Field(default="", max_length=256)
    status: PeopleStatus = ""
    tag: str = Field(default="", max_length=16)
    expires: int | None = Field(default=None, ge=1, le=365, strict=True)
    quota: int | None = Field(default=None, ge=1, le=100, strict=True)
    never: bool = False
    attention: Literal["", "all", "expiring", "quota", "unused", "stopped"] = ""
    lifecycle: PeopleLifecycle = ""
    sort: PeopleSort = "name"
    order: Literal["asc", "desc"] | None = None
    limit: int = Field(default=100, ge=1, le=500, strict=True)
    cursor: str | None = Field(default=None, max_length=2048)

    @field_validator("tag")
    @classmethod
    def valid_tag(cls, value: str) -> str:
        return value if value in ("", "none") else validate_person_tag(value)

    @field_validator("q")
    @classmethod
    def trim_search(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="before")
    @classmethod
    def default_order(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("order") is None:
            return {**value, "order": "desc" if value.get("sort") in ("usage", "activity") else "asc"}
        return value

    def selection_data(self) -> dict:
        """Paging controls never change who matches."""
        return self.model_dump(exclude={"limit", "cursor"})


class PeoplePerson(UserTrafficEvidence, AccountLifecycleState):
    """List evidence without connection credentials or private notes."""

    username: str
    user_id: str
    display_name: str
    declared: bool
    row_version: int = Field(ge=1)
    status: Literal["active", "disabled", "expired", "limited"]
    created_at: Timestamp
    expire_at: Timestamp | None
    traffic_limit_bytes: int = Field(ge=0)
    traffic_limit_strategy: LimitStrategy
    traffic_reset_at: Timestamp | None
    used_traffic_bytes: int = Field(ge=0)
    lifetime_traffic_bytes: int = Field(ge=0)
    period_traffic: PeriodTraffic
    online_at: Timestamp | None
    never_used: bool
    quota_used_percent: int | None
    attention_reasons: list[PersonAttentionReason]
    device_limit: DeviceCap | None
    tag: PersonTag
    language: Language | None = None
    schedule: AccountSchedulePreview | None


class PeopleFacets(CoreModel):
    status: dict[str, int] = Field(default_factory=dict)
    tag: dict[str, int] = Field(default_factory=dict)
    attention: dict[str, int] = Field(default_factory=dict)
    lifecycle: dict[str, int] = Field(default_factory=dict)
    expires: int = 0
    quota: int = 0
    never: int = 0


class PeoplePage(CoreModel):
    users: list[PeoplePerson]
    total: int = Field(ge=0, description="Matching people in this page sequence.")
    population: int = Field(ge=0)
    facets: PeopleFacets
    next_cursor: str | None
    observed_at: Timestamp
