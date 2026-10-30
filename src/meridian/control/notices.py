"""One operator notice in snapshot-covered meta, with content-free change events."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import UserRecord
from meridian.control.store import Store
from meridian.core.notices import Notice, NoticeState, NoticeWrite, RenderedNotice, substitute_notice

_NOTICE_KEY = "operator_notice"


def usage_text(size: int) -> str:
    """Short binary units with no markup or locale-dependent formatting syntax."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        if value < 1024 or unit == "EiB":
            return f"{value:g} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def render_notice(notice: Notice | None, user: UserRecord | None, now: datetime) -> RenderedNotice | None:
    if notice is None or notice.until is not None and datetime.fromisoformat(notice.until) <= now:
        return None
    expiry = datetime.fromisoformat(user.expire_at) if user is not None and user.expire_at else None
    values = {
        "name": user.username if user is not None else "person",
        "expires": expiry.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if expiry else "No expiry",
        "days_left": str(max(0, math.ceil((expiry - now).total_seconds() / 86400))) if expiry else "—",
        "used": usage_text(user.used_traffic_bytes if user is not None else 0),
        "quota": usage_text(user.traffic_limit_bytes) if user is not None and user.traffic_limit_bytes else "Unlimited",
    }
    return RenderedNotice(text=substitute_notice(notice.text, values), until=notice.until)


class NoticeLedger:
    def __init__(
        self,
        store: Store,
        events: EventLedger,
        *,
        read_user: Callable[[str], UserRecord | None],
        clock: Callable[[], float],
    ) -> None:
        self._store = store
        self._events = events
        self._read_user = read_user
        self._clock = clock

    def read(self) -> Notice | None:
        raw = self._store.meta(_NOTICE_KEY)
        return Notice.model_validate_json(raw) if raw else None

    def render(self, user: UserRecord) -> RenderedNotice | None:
        return render_notice(self.read(), user, datetime.fromtimestamp(self._clock(), UTC))

    def show(self, for_user: str | None = None) -> NoticeState:
        with self._store.lock:
            return self._state(self.read(), for_user)

    def update(self, request: NoticeWrite) -> NoticeState:
        if not request.confirm:
            with self._store.lock:
                self._require_future(request.notice)
                return self._state(request.notice, request.for_user)
        with self._store.transaction():
            self._require_future(request.notice)
            state = self._state(request.notice, request.for_user)
            if request.notice == self.read():
                return state
            self._store.set_meta(_NOTICE_KEY, request.notice.model_dump_json() if request.notice is not None else "")
            self._events.append("notice.changed", "notice")
            return state.model_copy(update={"changed": True})

    def _require_future(self, notice: Notice | None) -> None:
        if (
            notice is not None
            and notice.until is not None
            and datetime.fromisoformat(notice.until).timestamp() <= self._clock()
        ):
            raise ServiceError("invalid_request", "Use a future UTC time for the notice expiry, or omit it.", 400)

    def _state(self, notice: Notice | None, for_user: str | None) -> NoticeState:
        user = self._read_user(for_user) if for_user is not None else None
        if for_user is not None and user is None:
            raise ServiceError("not_found", "Unknown person. Check names with meridian client list.", 404)
        rendered = render_notice(notice, user, datetime.fromtimestamp(self._clock(), UTC))
        return NoticeState(
            notice=notice,
            active=rendered is not None,
            for_user=for_user,
            preview=rendered.text if rendered is not None else "",
        )
