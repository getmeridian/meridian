"""Translate one CLI limit change for both preview and apply."""

from meridian.control.models import UserUpdate
from meridian.core.account_schedule import LIFECYCLE_FIELDS
from meridian.core.client_limits import ClientLimitChange


def limit_update(change: ClientLimitChange) -> UserUpdate:
    fields = change.model_dump(include=change.model_fields_set & set(LIFECYCLE_FIELDS))
    if change.traffic_limit_bytes is not None:
        fields["traffic_limit_bytes"] = change.traffic_limit_bytes
    if change.traffic_reset is not None:
        fields["traffic_limit_strategy"] = "no_reset" if change.traffic_reset == "never" else change.traffic_reset
    if change.sets_expiry:
        fields["expire_at"] = change.expires_at
    if change.sets_devices:
        fields["device_limit"] = change.device_limit
    if change.sets_language:
        fields["language"] = change.language
    for field in ("note", "tag"):
        if getattr(change, field) is not None:
            fields[field] = getattr(change, field)
    return UserUpdate.model_validate(fields)
