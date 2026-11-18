"""Owner-managed subscription presentation, independent of network topology."""

from pydantic import Field, model_validator
from typing_extensions import Self

from meridian.core.inputs import ServerReferenceValue, ServerTitleValue
from meridian.core.models import CoreModel
from meridian.core.owner_contact import OwnerContact
from meridian.core.server_allowances import ServerMetadata, ServerMetadataChange
from meridian.core.subscription_formats import SubscriptionUpdateIntervalHours


class PresentationValueResult(CoreModel):
    value: str | int
    version: int = Field(ge=0)
    dry_run: bool = False


class PresentationState(CoreModel):
    version: int = Field(ge=0)
    # Old APIs accepted titles up to 100 characters; edits use the current wizard's validation.
    profile_title: str
    update_interval_hours: SubscriptionUpdateIntervalHours
    owner_contact: OwnerContact
    servers: dict[ServerReferenceValue, ServerMetadata]


class PresentationPatch(CoreModel):
    expected_version: int = Field(ge=0)
    profile_title: ServerTitleValue | None = None
    update_interval_hours: SubscriptionUpdateIntervalHours | None = None
    owner_contact: OwnerContact | None = None
    servers: dict[ServerReferenceValue, ServerMetadataChange] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_values(self) -> Self:
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Omit unchanged settings. Use an empty contact to clear it.")
        if self.model_fields_set == {"expected_version"}:
            raise ValueError("Choose a presentation setting to change.")
        return self

    def updated(self, current: PresentationState) -> PresentationState:
        values = current.model_dump()
        for field in ("profile_title", "update_interval_hours", "owner_contact"):
            if field in self.model_fields_set:
                values[field] = getattr(self, field)
        for ref, change in self.servers.items():
            if ref not in current.servers:
                raise KeyError(ref)
            values["servers"][ref] = change.apply_to(current.servers[ref])
        return PresentationState.model_validate(values)
