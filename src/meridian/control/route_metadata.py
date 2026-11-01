"""Shared error and credential annotations for administrative route catalogs."""

from meridian.control.route_types import BodyScope, ErrorResponse

NOT_FOUND = ErrorResponse(404, ("not_found",))
CONFLICT = ErrorResponse(409, ("conflict",))
HISTORY_OFF = ErrorResponse(409, ("history_off",))
CUT_OFF_ERRORS = ErrorResponse(409, ("declared_user", "conflict", "command_queue_full"))
CUT_OFF_SCOPE = (BodyScope("cut_off", True, "admin"),)


USER_CREDENTIALS = (
    "User UUIDs and subscription tokens are blank unless the caller has people or admin scope; "
    "private notes and tags follow the same rule."
)
DECLARED_USER = (
    "People tokens cannot remove or reissue a person declared by an approved, applied, or failed fleet revision; "
    "use the meridian CLI."
)
