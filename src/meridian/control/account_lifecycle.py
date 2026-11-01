"""Account schedule validation, partial edits, and transactional catch-up."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import UserRecord, UserUpdate
from meridian.control.store import Store
from meridian.control.user_expiry import require_finite_user_expiry
from meridian.control.user_policy import enforced_status
from meridian.core.account_schedule import (
    AccountSchedulePreview,
    activation_expiry,
    calculate_schedule,
    period_start,
    timestamp,
)


def validate_user_schedule(user: UserRecord, now: str) -> AccountSchedulePreview:
    """Refuse schedules that cannot be activated or renewed before writing anything."""
    require_finite_user_expiry(user.expire_at)
    if user.renewal_enabled and user.expire_at is None and user.activation_duration_days is None:
        raise ServiceError(
            "invalid_request", "Automatic renewal needs a fixed expiry or a duration from first use.", 400
        )
    if user.activation_duration_days is not None and user.expire_at is None:
        if user.first_traffic_at is None and user.lifetime_traffic_bytes > 0:
            raise ServiceError("invalid_request", "First use is unknown for this account. Set a fixed expiry.", 400)
    if user.renewals_completed and user.renewal_anchor_at is None:
        raise ServiceError("invalid_request", "Renewal progress needs the original renewal expiry.", 400)
    try:
        return calculate_schedule(user, datetime.fromisoformat(now))
    except (ValueError, OverflowError) as exc:
        raise ServiceError(
            "invalid_request", "The schedule must end before 2099. Use a shorter interval.", 400
        ) from exc


def merge_user_update(user: UserRecord, update: UserUpdate, now: str) -> UserRecord:
    """Choose a mode explicitly and preserve untouched duration, interval, cap, and progress."""
    changes = update.model_dump(include=update.model_fields_set)
    for field in ("status", "traffic_limit_bytes", "traffic_limit_strategy"):
        if field in changes and changes[field] is None:
            del changes[field]
    duration_set = "activation_duration_days" in changes and changes["activation_duration_days"] is not None
    expiry_set = "expire_at" in changes
    if duration_set and changes.get("expire_at") is not None:
        raise ServiceError("invalid_request", "Choose a fixed expiry or a duration from first use.", 400)
    if duration_set:
        changes["expire_at"] = None
    elif expiry_set:
        changes["activation_duration_days"] = None
    if "traffic_reset_anchor_day" in changes and changes["traffic_reset_anchor_day"] is None:
        changes["traffic_reset_anchor_day"] = datetime.fromisoformat(user.created_at).astimezone(UTC).day
    if changes.get("renewal_interval_days") is not None:
        changes["renewal_interval_months"] = None
    if changes.get("renewal_interval_months") is not None:
        changes["renewal_interval_days"] = None
    interval_changed = any(
        field in changes and changes[field] != getattr(user, field)
        for field in ("renewal_interval_days", "renewal_interval_months")
    )
    if expiry_set or duration_set or interval_changed:
        changes["renewals_completed"] = 0
        enabled = any(
            changes.get(field, getattr(user, field)) is not None
            for field in ("renewal_interval_days", "renewal_interval_months")
        )
        changes["renewal_anchor_at"] = changes.get("expire_at", user.expire_at) if enabled else None
    merged = UserRecord.model_validate({**user.model_dump(), **changes})
    validate_user_schedule(merged, now)
    return merged


def preview_user_schedule(
    store: Store, request: UserSchedulePreviewRequest, *, now: str, read_user: Callable[[str], UserRecord | None]
) -> AccountSchedulePreview:
    """Read one account view, or use creation defaults, and keep every table unchanged."""
    with store.lock:
        user = (
            read_user(request.username)
            if request.username is not None
            else UserRecord(
                username="preview",
                user_id="preview",
                uuid="",
                sub_token="",
                status="active",
                created_at=now,
                traffic_reset_anchor_day=datetime.fromisoformat(now).astimezone(UTC).day,
            )
        )
        if user is None:
            raise ServiceError("not_found", "Unknown user.", 404)
        return validate_user_schedule(merge_user_update(user, request.change, now), now)


def enforce_user_schedules(
    store: Store,
    users: Iterable[UserRecord],
    now: str,
    *,
    read_user: Callable[[str], UserRecord],
    reset_traffic: Callable[[UserRecord, str], None],
    events: EventLedger,
) -> list[str]:
    """Activate and catch up once in the caller's transaction, then judge all remaining reasons."""
    moment = datetime.fromisoformat(now)
    changed: list[str] = []
    for user in users:
        preview = calculate_schedule(user, moment)
        previous_expiry = datetime.fromisoformat(user.expire_at) if user.expire_at else None
        next_expiry = datetime.fromisoformat(preview.expire_at) if preview.expire_at else None
        expiry_changed = previous_expiry != next_expiry
        if expiry_changed or preview.renewals_due:
            anchor = user.renewal_anchor_at or timestamp(activation_expiry(user))
            store.db.execute(
                "UPDATE users SET expire_at = ?, renewals_completed = ?, renewal_anchor_at = ? WHERE username = ?",
                (
                    preview.expire_at,
                    preview.renewals_completed,
                    anchor if user.renewal_enabled else None,
                    user.username,
                ),
            )
            updated = read_user(user.username)
            events.user_updated(user, updated, actor="system", now=now)
            changed.append(user.username)
            user = updated
        start = period_start(user.traffic_limit_strategy, moment, user.traffic_reset_anchor_day)
        period_due = start is not None and (
            user.traffic_reset_at is None or datetime.fromisoformat(user.traffic_reset_at) < start
        )
        if preview.renewals_due or period_due:
            reset_traffic(user, now)
            events.append("user.traffic_reset", user.username, actor="system", now=now)
            user = read_user(user.username)
        status = enforced_status(user, now)
        if status != user.status:
            events.enforcement(user.username, user.status, status, now)
            store.db.execute("UPDATE users SET status = ? WHERE username = ?", (status, user.username))
            if user.username not in changed:
                changed.append(user.username)
    return changed
