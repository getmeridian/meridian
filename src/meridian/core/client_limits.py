"""Client traffic limits and expiry: operator input, the typed change, and panel-value normalization.

Both control backends (Remnawave and Meridian's control service) take one
`ClientLimitChange`; each adapter maps it onto its own API. Sizes are binary
(1 GiB = 1024³ bytes; GB remains an input alias). A bare date
expires at the end of that day in UTC (`2026-12-31` → `2026-12-31T23:59:59Z`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from pydantic import Field, field_validator

from meridian.core.agent_types import Timestamp
from meridian.core.calendar import month_date, shift_month
from meridian.core.errors import MeridianError
from meridian.core.languages import Language, parse_person_language
from meridian.core.models import CoreModel
from meridian.core.person_metadata import PersonTag, PrivateNote, parse_note_option, parse_tag_option

TrafficReset = Literal["never", "day", "week", "month"]
TRAFFIC_RESETS: tuple[TrafficReset, ...] = ("never", "day", "week", "month")
LimitStrategy = Literal["no_reset", "day", "week", "month"]

# Remnawave needs an expiry on every user, so Meridian stores "never" there as a far-future date;
# any expiry from this year on reads back as "never", and operators cannot type one.
NEVER_EXPIRES_YEAR = 2099

_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
# The control service keeps limits in a SQLite INTEGER: a signed 64-bit value.
MAX_TRAFFIC_LIMIT_BYTES = 2**63 - 1
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgt]?i?b)$")
_UNLIMITED = frozenset({"0", "none", "unlimited", "off"})
_NEVER = frozenset({"never", "none", "off"})
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_END_OF_DAY = time(23, 59, 59)


class ClientLimitError(MeridianError):
    """A traffic limit, reset period, or expiry the operator typed cannot be used."""

    def __init__(self, message: str, *, hint: str) -> None:
        super().__init__(message, hint=hint, category="user")


class UserCreationSettings(CoreModel):
    """First service-owned settings, applied only when an access account is created."""

    status: Literal["active", "disabled"] = "active"
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int = Field(default=0, ge=0, le=MAX_TRAFFIC_LIMIT_BYTES)
    traffic_limit_strategy: LimitStrategy = "no_reset"
    device_limit: int | None = Field(default=None, ge=1, le=100)


class ClientExtendRequest(CoreModel):
    """Add whole days after the later of now and the person's current expiry."""

    days: int = Field(ge=1, le=3650, strict=True)


class CalendarRenewalRequest(CoreModel):
    """One- or three-month renewal preview; it never changes an account by itself."""

    months: int = Field(ge=1, le=3, strict=True, json_schema_extra={"enum": [1, 3]})

    @field_validator("months")
    @classmethod
    def supported_months(cls, value: int) -> int:
        if value not in (1, 3):
            raise ValueError("Choose +1 month or +3 months.")
        return value


class CalendarExpiryPreview(CoreModel):
    """Whole-second UTC base and expiry for a calendar renewal."""

    base_at: Timestamp
    expire_at: Timestamp
    months: int


def preview_calendar_expiry(
    current: str | None, request: CalendarRenewalRequest, *, now: datetime | None = None
) -> CalendarExpiryPreview:
    """Advance max(now, current expiry) by calendar months, retaining its UTC time."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    current_moment = _parse_moment(current) if current else None
    base = max(moment, current_moment or moment).replace(microsecond=0)
    year, month = shift_month(base.year, base.month, request.months)
    day = month_date(year, month, base.day)
    renewed = base.replace(year=day.year, month=day.month, day=day.day)
    expire_at = parse_expiry(renewed.isoformat(), now=moment)
    assert expire_at is not None
    return CalendarExpiryPreview(base_at=base.strftime(_TIMESTAMP_FORMAT), expire_at=expire_at, months=request.months)


class ClientLimitChange(CoreModel):
    """A partial change to one client's limits; omitted fields keep their value.

    `expires_at: null` (set explicitly) means the client never expires.
    """

    traffic_limit_bytes: int | None = Field(
        default=None, ge=0, le=MAX_TRAFFIC_LIMIT_BYTES, description="0 means unlimited."
    )
    traffic_reset: TrafficReset | None = Field(default=None, description="When used traffic starts again from zero.")
    expires_at: Timestamp | None = Field(default=None, description="UTC; null with the field set means never.")
    device_limit: int | None = Field(default=None, ge=1, le=100, description="Explicit null uses the fleet default.")
    note: PrivateNote | None = Field(
        default=None, repr=False, description="Omitted keeps the private note; empty clears."
    )
    tag: PersonTag | None = Field(default=None, repr=False, description="Omitted keeps the private tag; empty clears.")
    language: Language | None = Field(default=None, description="Explicit null uses the fleet's subscription language.")

    @property
    def is_empty(self) -> bool:
        return not self.model_fields_set

    @property
    def sets_expiry(self) -> bool:
        return "expires_at" in self.model_fields_set

    @property
    def sets_devices(self) -> bool:
        return "device_limit" in self.model_fields_set

    @property
    def sets_language(self) -> bool:
        return "language" in self.model_fields_set

    def for_creation(self) -> UserCreationSettings:
        """Use creation defaults for fields the operator did not specify."""
        reset = self.traffic_reset
        strategy: LimitStrategy = "no_reset"
        if reset is not None and reset != "never":
            strategy = reset
        return UserCreationSettings(
            expire_at=self.expires_at,
            traffic_limit_bytes=self.traffic_limit_bytes or 0,
            traffic_limit_strategy=strategy,
            device_limit=self.device_limit,
        )


def build_limit_change(
    *,
    limit: str | None = None,
    reset: str | None = None,
    expires: str | None = None,
    devices: str | None = None,
    note: str | None = None,
    tag: str | None = None,
    language: str | None = None,
    now: datetime | None = None,
) -> ClientLimitChange:
    """Parse the `--limit`, `--reset`, and `--expires` values that were given into one change."""
    fields: dict[str, object] = {}
    if limit is not None:
        fields["traffic_limit_bytes"] = parse_traffic_limit(limit)
    if reset is not None:
        fields["traffic_reset"] = parse_traffic_reset(reset)
    if expires is not None:
        fields["expires_at"] = parse_expiry(expires, now=now)
    if devices is not None:
        fields["device_limit"] = parse_device_limit(devices)
    if note is not None:
        fields["note"] = parse_note_option(note)
    if tag is not None:
        fields["tag"] = parse_tag_option(tag)
    if language is not None:
        fields["language"] = parse_person_language(language)
    return ClientLimitChange(**fields)  # type: ignore[arg-type]


def parse_device_limit(text: str) -> int | None:
    """A per-user cap, or None for the control service's default; no value disables fleet privacy settings."""
    value = _normalized(text)
    if value == "default":
        return None
    if value.isascii() and value.isdigit() and len(value) <= 3 and 1 <= int(value) <= 100:
        return int(value)
    raise ClientLimitError(
        f"Device limit '{text}' is not between 1 and 100",
        hint="Use --devices 1 to 100, or --devices default for the fleet's cap.",
    )


def parse_traffic_limit(text: str) -> int:
    """Bytes for `50GB`, `500MB`, `1.5TB` (binary units); 0 for `none`, `unlimited`, or `0`."""
    value = _normalized(text).replace(" ", "")
    if value in _UNLIMITED:
        return 0
    match = _SIZE_RE.match(value)
    if match is None:
        raise ClientLimitError(
            f"Traffic limit '{text}' is not a size",
            hint="Use a number with a unit, such as 500MiB, 50GiB, or 1.5TiB, or `none` for no limit.",
        )
    number, unit = match.groups()
    # Decimal, not float: a long digit string would become infinity, and round() then raises.
    size = int((Decimal(number) * _UNITS[unit.replace("i", "")]).to_integral_value(rounding=ROUND_HALF_EVEN))
    if size > MAX_TRAFFIC_LIMIT_BYTES:
        raise ClientLimitError(
            f"Traffic limit '{text}' is too large",
            hint=f"Use at most {MAX_TRAFFIC_LIMIT_BYTES // _UNITS['tb']}TiB, or `none` for no limit.",
        )
    if size < 1:
        raise ClientLimitError(
            f"Traffic limit '{text}' is too small",
            hint="Use `none` (or 0) to remove the limit.",
        )
    return size


def parse_traffic_reset(text: str) -> TrafficReset:
    """`day`, `week`, `month`, or `never` (also `daily`, `weekly`, `monthly`)."""
    value = _normalized(text).removesuffix("ly").replace("dai", "day")
    if value in _NEVER or value == "no_reset":
        return "never"
    for period in TRAFFIC_RESETS:
        if value == period:
            return period
    raise ClientLimitError(
        f"Reset period '{text}' is not one of day, week, month, never",
        hint="Use --reset day, week, month, or never (calendar periods).",
    )


def parse_expiry(text: str, *, now: datetime | None = None) -> str | None:
    """A UTC timestamp for a date (end of that day, UTC) or an ISO timestamp; None for `never`."""
    value = _normalized(text)
    if value in _NEVER:
        return None
    moment = _parse_moment(value)
    if moment is None:
        raise ClientLimitError(
            f"Expiry '{text}' is not a date",
            hint="Use YYYY-MM-DD (the end of that day, UTC), an ISO time such as 2026-12-31T18:00:00+03:00, "
            "or `never`.",
        )
    if moment.year >= NEVER_EXPIRES_YEAR:
        raise ClientLimitError(
            f"Expiry '{text}' is too far ahead",
            hint=f"Use --expires never for no expiry, or a date before {NEVER_EXPIRES_YEAR}.",
        )
    if moment <= (now or datetime.now(UTC)):
        raise ClientLimitError(
            f"Expiry '{text}' is in the past",
            hint="Use a future date, or `meridian client disable` to cut a client off now.",
        )
    return moment.strftime(_TIMESTAMP_FORMAT)


def canonical_expiry(raw: str) -> str:
    """A panel's expiry as a whole-second UTC timestamp; empty for none, unreadable, or "never" values."""
    moment = _parse_moment(raw.strip()) if raw else None
    if moment is None or moment.year >= NEVER_EXPIRES_YEAR:
        return ""
    return moment.strftime(_TIMESTAMP_FORMAT)


def traffic_reset_from_strategy(raw: str) -> str:
    """A panel's reset strategy in Meridian's words: never, day, week, month (or month_rolling, panel-only)."""
    value = raw.strip().lower()
    return "never" if value in ("", "no_reset") else value


def _parse_moment(value: str) -> datetime | None:
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), _END_OF_DAY, UTC)
        moment = datetime.fromisoformat(value.upper())
    except ValueError:
        return None
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return moment.replace(microsecond=0)


def _normalized(text: str) -> str:
    """Lowercase and strip, with any script's digits (Persian, Arabic-Indic, …) as ASCII."""
    return "".join(
        str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text.strip().lower()
    ).replace("٫", ".")
