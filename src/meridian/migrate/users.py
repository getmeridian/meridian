"""Turn the panel's users into control-service imports, and say what does not carry over.

Only `topology_intent.access.users` move: the panel's service users and anything
an operator created by hand stay behind. Each import keeps the VLESS UUID (also
the Hysteria2 password) and the short UUID (the subscription token), so every
link keeps working, plus status, expiry, limit, reset strategy, and usage.
Nothing IP- or device-based is imported.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from meridian.control.models import SUB_TOKEN_PATTERN, LimitStrategy, UserImport, UserImportBatch, UserStatus
from meridian.core.client_limits import NEVER_EXPIRES_YEAR
from meridian.core.errors import previous_release_command
from meridian.core.person_metadata import MAX_NOTE_CHARACTERS, validate_person_tag
from meridian.migrate import MigrationError
from meridian.remnawave import User

_STATUSES: dict[str, UserStatus] = {
    "ACTIVE": "active",
    "DISABLED": "disabled",
    "LIMITED": "limited",
    "EXPIRED": "expired",
}
_STRATEGIES: dict[str, LimitStrategy] = {
    "": "no_reset",
    "NO_RESET": "no_reset",
    "DAY": "day",
    "WEEK": "week",
    "MONTH": "month",
    # The service resets on calendar boundaries only; the closest is the calendar month.
    "MONTH_ROLLING": "month",
}
_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|\+00:00)$")

# Fleet-wide panel state intentionally left behind; reported at review, gone with the panel.
DROPPED_PANEL_STATE: tuple[str, ...] = (
    "the panel's admin account and API tokens",
    "usage history per node and per day, online and first-connection times",
    "subscription request history and HWID device registrations (never imported, even with Meridian device limits on)",
    "panel plugins (Torrent Blocker, IP Control) and response rules Meridian does not manage",
    "the Remnawave subscription page (Meridian's own connection pages stay)",
)


@dataclass(frozen=True)
class CarriedUsers:
    """What the import creates, and what Remnawave held that it leaves behind."""

    batch: UserImportBatch
    notes: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def carry_users(panel_users: Sequence[User], wanted: Sequence[str], *, deployed_with: str = "") -> CarriedUsers:
    """Map the intent's users from the panel; refuse what cannot be imported without breaking a link."""
    by_name = {user.username: user for user in panel_users}
    missing = sorted(set(wanted) - set(by_name))
    if missing:
        raise MigrationError(
            "The panel has no user for: " + ", ".join(missing),
            hint=(
                "Converge the fleet with the release that deployed it, then migrate: "
                + previous_release_command(deployed_with, "apply")
            ),
            category="user",
        )
    unfit = sorted(name for name in wanted if not re.fullmatch(SUB_TOKEN_PATTERN, by_name[name].short_uuid))
    if unfit:
        raise MigrationError(
            "These users' subscription IDs do not fit Meridian's link format: " + ", ".join(unfit),
            hint="Give each a new link with `meridian client reissue NAME`, then migrate.",
            category="user",
        )
    imports: list[UserImport] = []
    notes: dict[str, list[str]] = {}
    for name in wanted:
        user = by_name[name]
        note, tag, metadata_notes = _metadata(user)
        imports.append(_import(user, note=note, tag=tag))
        dropped = [*_dropped(user), *metadata_notes]
        if dropped:
            notes[name] = dropped
    skipped = sorted(set(by_name) - set(wanted))
    return CarriedUsers(batch=UserImportBatch(users=imports), notes=notes, skipped=skipped)


def _import(user: User, *, note: str, tag: str) -> UserImport:
    status = _STATUSES.get(user.status.upper())
    strategy = _STRATEGIES.get(user.traffic_limit_strategy.upper())
    if status is None or strategy is None:
        raise MigrationError(
            f"User {user.username!r} has a status or reset strategy Meridian does not know: "
            f"{user.status!r}, {user.traffic_limit_strategy!r}.",
            hint="Upgrade Meridian, or change the user in the panel, then migrate.",
            category="user",
        )
    try:
        return UserImport(
            username=user.username,
            uuid=user.vless_uuid,
            sub_token=user.short_uuid,
            status=status,
            expire_at=_expiry(user.expire_at),
            traffic_limit_bytes=max(0, user.traffic_limit_bytes),
            traffic_limit_strategy=strategy,
            used_traffic_bytes=max(0, user.used_traffic_bytes),
            lifetime_traffic_bytes=max(0, user.lifetime_used_traffic_bytes),
            traffic_reset_at=_timestamp(user.last_traffic_reset_at),
            created_at=_timestamp(user.created_at),
            note=note,
            tag=tag,
        )
    except ValueError as exc:
        raise MigrationError(
            f"User {user.username!r} cannot be imported: a field is invalid.",
            hint="Check the user in the panel, then migrate.",
            category="user",
        ) from exc


def _dropped(user: User) -> list[str]:
    dropped: list[str] = []
    if user.traffic_limit_strategy.upper() == "MONTH_ROLLING":
        dropped.append("monthly reset on the user's own day becomes the calendar month (1st, UTC)")
    if user.hwid_device_limit is not None:
        dropped.append(f"HWID device limit {user.hwid_device_limit}")
    for label, value in (
        ("email", user.email),
        ("Telegram ID", user.telegram_id),
    ):
        if value:
            dropped.append(label)
    return dropped


def _metadata(user: User) -> tuple[str, str, list[str]]:
    reports: list[str] = []
    text = user.description.replace("\r\n", "\n").replace("\r", "\n")
    note = "".join(
        character for character in text if character == "\n" or unicodedata.category(character) not in {"Cc", "Cs"}
    )
    if note != text:
        reports.append("description control characters removed")
    if len(note) > MAX_NOTE_CHARACTERS:
        note = note[:MAX_NOTE_CHARACTERS]
        reports.append("description truncated to 500 characters")
    try:
        tag = validate_person_tag(user.tag)
    except ValueError:
        tag = ""
        reports.append("invalid tag")
    return note, tag, reports


def _timestamp(value: str) -> str | None:
    match = _TIMESTAMP.match(value or "")
    return f"{match[1]}Z" if match else None


def _expiry(value: str) -> str | None:
    """Remnawave's "never" is a 2099 date; Meridian's is no expiry."""
    timestamp = _timestamp(value)
    if timestamp is None or int(timestamp[:4]) >= NEVER_EXPIRES_YEAR:
        return None
    return timestamp
