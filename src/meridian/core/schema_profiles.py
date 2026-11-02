"""Access profile schemas and CLI envelopes, composed from their owning contracts."""

from typing import Literal

from pydantic import BaseModel

from meridian.control.access_profile_models import ServiceSettingsWrite
from meridian.core.access_profile_views import (
    AccessProfileCatalog,
    AccessProfileReview,
    AccessProfileReviewRequest,
    PersonAccess,
)
from meridian.core.access_profiles import AccessProfileIntent
from meridian.core.profile_commands import ProfileEditResult, ProfileListResult
from meridian.core.schema_envelopes import make_command_envelope

PROFILE_SCHEMAS: dict[str, type[BaseModel]] = {
    "access-profile-intent": AccessProfileIntent,
    "access-profile-catalog": AccessProfileCatalog,
    "access-profile-review": AccessProfileReview,
    "access-profile-review-request": AccessProfileReviewRequest,
    "person-access": PersonAccess,
    "profile-list": ProfileListResult,
    "profile-edit": ProfileEditResult,
    "service-settings-write": ServiceSettingsWrite,
}


def profile_command_schemas(base: type[BaseModel], empty: type[BaseModel]) -> dict[str, type[BaseModel]]:
    schemas: dict[str, type[BaseModel]] = {}
    for action in ("add", "set", "remove", "list"):
        _success, _terminal, envelope = make_command_envelope(
            "Profile" + action.title(),
            "profile." + action,
            ProfileListResult if action == "list" else ProfileEditResult,
            envelope_base=base,
            failure_data_type=empty,
            success_statuses=Literal["ok", "changed", "no_changes"],
            doc="Envelope for meridian profile " + action + " --json.",
        )
        schemas["profile-" + action + "-envelope"] = envelope
    return schemas
