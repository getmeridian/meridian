"""Shared error and credential annotations for administrative route catalogs."""

from meridian.control.route_types import BodyScope, ErrorResponse, ExtraCapability

NOT_FOUND = ErrorResponse(404, ("not_found",))
CONFLICT = ErrorResponse(409, ("conflict",))
NAME_CONFLICT = ErrorResponse(409, ("name_conflict",))
HISTORY_OFF = ErrorResponse(409, ("history_off",))
CUT_OFF_ERRORS = ErrorResponse(409, ("declared_user", "conflict", "command_queue_full"))
CUT_OFF_SCOPE = (BodyScope("cut_off", True, "admin", "commands.cut_off"),)
USER_RESPONSE_CAPABILITIES = (
    ExtraCapability("credentials", "people.credentials", "people"),
    ExtraCapability("private_metadata", "people.private", "people"),
)
DECLARED_CAPABILITY = ExtraCapability("declared_people", "fleet.apply", "admin")
REVISION_RESPONSE_CAPABILITIES = (ExtraCapability("private_membership", "people.private", "people"),)


USER_CREDENTIALS = (
    "User UUIDs and subscription tokens need people.credentials; "
    "private notes, tags, profiles, and delivery-policy overrides need people.private. "
    "These fields are otherwise blank, including in mutation responses. People and admin presets include both."
)
DECLARED_USER = (
    "Removing or reissuing a person declared by an approved, applied, or failed revision needs fleet.apply. "
    "Owner sessions use the meridian CLI."
)
