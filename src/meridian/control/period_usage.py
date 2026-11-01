"""Directional period baselines; imported and unrecoverable old splits stay explicit."""

from __future__ import annotations

from meridian.control.models import UserRecord
from meridian.control.store import Store

# 17 -> 18: no old period byte is lost or assigned a guessed direction. A native
# lifetime with no offset has its real split; all other old periods are carried
# as unattributed download until the next reset. Future growth is directional.
_NATIVE_LIFETIME = "traffic_offset_bytes = 0 AND traffic_carried_bytes = 0"
_UPLOAD = "COALESCE((SELECT uplink_bytes FROM traffic_totals WHERE user_id = users.user_id), 0)"
_DOWNLOAD = "COALESCE((SELECT downlink_bytes FROM traffic_totals WHERE user_id = users.user_id), 0)"
DIRECTIONAL_PERIOD_MIGRATION = (
    "ALTER TABLE users ADD COLUMN traffic_upload_offset_bytes INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN traffic_download_offset_bytes INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN traffic_period_carried_bytes INTEGER NOT NULL DEFAULT 0",
    f"UPDATE users SET traffic_upload_offset_bytes = CASE WHEN {_NATIVE_LIFETIME} THEN 0 ELSE {_UPLOAD} END,"
    f" traffic_download_offset_bytes = CASE WHEN {_NATIVE_LIFETIME} THEN 0 ELSE {_DOWNLOAD} END,"
    f" traffic_period_carried_bytes = CASE WHEN {_NATIVE_LIFETIME} THEN 0"
    f" ELSE MAX(0, {_UPLOAD} + {_DOWNLOAD} + traffic_carried_bytes - traffic_offset_bytes) END",
)


def lifetime_directions(store: Store, username: str) -> tuple[int, int]:
    row = store.db.execute(
        "SELECT COALESCE(t.uplink_bytes, 0), COALESCE(t.downlink_bytes, 0)"
        " FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id WHERE u.username = ?",
        (username,),
    ).fetchone()
    return int(row[0]), int(row[1])


def reset_period(store: Store, user: UserRecord, now: str) -> None:
    """Start an empty period in the caller's transaction, preserving lifetime totals."""
    up, down = lifetime_directions(store, user.username)
    store.db.execute(
        "UPDATE users SET traffic_offset_bytes = ?, traffic_upload_offset_bytes = ?,"
        " traffic_download_offset_bytes = ?, traffic_period_carried_bytes = 0, traffic_reset_at = ? WHERE username = ?",
        (user.lifetime_traffic_bytes, up, down, now, user.username),
    )


def import_period(store: Store, username: str, used_bytes: int) -> None:
    """An import starts from its combined period use, beside unchanged lifetime accounting."""
    up, down = lifetime_directions(store, username)
    store.db.execute(
        "UPDATE users SET traffic_offset_bytes = ? + traffic_carried_bytes - ?,"
        " traffic_upload_offset_bytes = ?, traffic_download_offset_bytes = ?, traffic_period_carried_bytes = ?"
        " WHERE username = ?",
        (up + down, used_bytes, up, down, used_bytes, username),
    )
