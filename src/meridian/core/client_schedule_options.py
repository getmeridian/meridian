"""Parse CLI schedule choices into validated operator-owned fields."""

from __future__ import annotations

from pydantic import ValidationError

from meridian.core.account_schedule import AccountLifecycleSettings
from meridian.core.errors import MeridianError


class ClientScheduleError(MeridianError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__("The account schedule is invalid.", category="user")


def parse_schedule_options(
    *,
    reset_day: str | None = None,
    duration: str | None = None,
    renew_days: str | None = None,
    renew_months: str | None = None,
    renewals: str | None = None,
    no_renew: bool = False,
) -> dict[str, object]:
    if (renew_days is not None and renew_months is not None) or (
        no_renew and any(value is not None for value in (renew_days, renew_months, renewals))
    ):
        raise ClientScheduleError("interval")
    choices = {
        "traffic_reset_anchor_day": reset_day,
        "activation_duration_days": duration,
        "renewal_interval_days": renew_days,
        "renewal_interval_months": renew_months,
        "renewal_limit": renewals,
    }
    fields: dict[str, object] = {}
    for field, value in choices.items():
        if value is None:
            continue
        text = value.strip().lower()
        clearing = {
            "traffic_reset_anchor_day": {"default", "creation"},
            "activation_duration_days": {"none"},
            "renewal_limit": {"unlimited", "none"},
        }
        if text in clearing.get(field, set()):
            fields[field] = None
        elif text.isdecimal() and len(text) <= 10:
            fields[field] = int(text)
        else:
            raise ClientScheduleError(field)
    if renew_days is not None:
        fields["renewal_interval_months"] = None
    if renew_months is not None:
        fields["renewal_interval_days"] = None
    if no_renew:
        fields.update(renewal_interval_days=None, renewal_interval_months=None, renewal_limit=None)
    try:
        AccountLifecycleSettings.model_validate(fields)
    except ValidationError as exc:
        raise ClientScheduleError(str(exc.errors()[0]["loc"][0])) from exc
    return fields
