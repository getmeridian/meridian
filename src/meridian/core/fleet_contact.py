"""Request and result contracts for reviewed owner contact edits."""

from __future__ import annotations

from pydantic import field_validator

from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.models import CoreModel
from meridian.core.owner_contact import ContactLink, ContactText, OwnerContact


class FleetContactRequest(CoreModel):
    text: ContactText | None = None
    link: ContactLink | None = None
    dry_run: bool = False
    yes: bool = False

    @field_validator("link", mode="before")
    @classmethod
    def clear_link(cls, value: object) -> object:
        return "" if value == "none" else value

    @property
    def is_edit(self) -> bool:
        return self.text is not None or self.link is not None

    def updated(self, current: OwnerContact) -> OwnerContact:
        fields = current.model_dump()
        if self.text is not None:
            fields["text"] = self.text
        if self.link is not None:
            fields["link"] = self.link
        return OwnerContact.model_validate(fields)


class FleetContactResult(CoreModel):
    owner_contact: OwnerContact
    dry_run: bool = False
    review: CompiledApplyPreview | None = None
    applied: CompiledApplyResult | None = None
