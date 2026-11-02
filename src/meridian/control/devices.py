"""Subscription device caps: keyed fingerprints in private mode, details only in full mode.

Every write runs in the caller's Store transaction, including registration and
privacy downgrades. No request address is read or stored.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from meridian.control.models import DeviceLimitLevel, PrivacySettings, UserDevices, UserRecord
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.core.client_devices import (
    DEVICE_KEY_PATTERN,
    ClientDevice,
    ClientDeviceDetails,
    DevicesRemoved,
    effective_device_limit,
)

DEVICES_TABLE = """CREATE TABLE IF NOT EXISTS devices (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    device_key TEXT NOT NULL,
    platform TEXT,
    os_version TEXT,
    model TEXT,
    user_agent TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    PRIMARY KEY (user_id, device_key)
)"""
DEVICE_HMAC_SECRET = "device_hmac_key"
MAX_HEADER_LENGTH = 512
_HWID = re.compile(DEVICE_KEY_PATTERN)
_COLUMNS = "device_key, platform, os_version, model, user_agent, first_seen_at, last_seen_at"


class HeaderReader(Protocol):
    def get(self, name: str) -> str | None: ...


def browser_request(user_agent: str | None, accept: str | None) -> bool:
    """The connection page's own fetch and browser navigations never register devices, even with a suffix."""
    return "text/html" in (accept or "").lower() or (user_agent or "").lstrip().lower().startswith("mozilla/")


def extract_hwid(headers: HeaderReader) -> str | None:
    """Remnawave's supported HWID vocabulary; an absent or invalid ID bypasses counting."""
    value = headers.get("x-hwid")
    return value if value is not None and _HWID.fullmatch(value) else None


def _header(headers: HeaderReader, name: str) -> str | None:
    value = headers.get(name)
    return value[:MAX_HEADER_LENGTH] if value is not None else None


class DeviceLedger:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    def fetch(self, user: UserRecord, privacy: PrivacySettings, headers: HeaderReader) -> dict[str, str]:
        """Register an active app's device atomically; response flags say when a new device is refused.

        The caller holds a transaction and has excluded off mode and browsers. Private
        rows keep only a fingerprint; even first/last seen remain NULL there.
        """
        result = {"x-hwid-active": "true"}
        hwid = extract_hwid(headers)
        if hwid is None:
            return result
        if (
            user.status != "active"
            and privacy.device_limits == "private"
            and self._store.secret(DEVICE_HMAC_SECRET) is None
        ):
            return result
        key = self._fingerprint(hwid) if privacy.device_limits == "private" else hwid
        row = self._store.row(
            "SELECT device_key, first_seen_at, last_seen_at FROM devices WHERE user_id = ? AND device_key = ?",
            (user.user_id, key),
        )
        # In full mode only a pending private fingerprint lacks a first-seen time; an app sending
        # that key as its own ID is not that device and must never take over its slot.
        if row is not None and privacy.device_limits == "full" and row["first_seen_at"] is None:
            return result
        # A raise from private to full learns the raw ID on its next fetch, without consuming another slot.
        if row is None and privacy.device_limits == "full" and self._store.secret(DEVICE_HMAC_SECRET) is not None:
            row = self._store.row(
                "SELECT device_key, first_seen_at, last_seen_at FROM devices"
                " WHERE user_id = ? AND device_key = ? AND first_seen_at IS NULL",
                (user.user_id, self._fingerprint(hwid)),
            )
        if row is None:
            if user.status != "active":
                return result
            count = self._count(user.user_id)
            cap = effective_device_limit(user.device_limit, privacy.default_device_limit)
            if cap != 0 and count >= cap:
                return {**result, "x-hwid-max-devices-reached": "true", "x-hwid-limit": "true"}
            self._store.db.execute("INSERT INTO devices (user_id, device_key) VALUES (?, ?)", (user.user_id, key))
        if privacy.device_limits == "full":
            now = self._clock()
            if (
                row is None
                or row["last_seen_at"] is None
                or now - datetime.fromisoformat(row["last_seen_at"]).timestamp() >= 60
            ):
                timestamp = format_timestamp(now)
                self._store.db.execute(
                    "UPDATE devices SET device_key = ?, platform = COALESCE(?, platform),"
                    " os_version = COALESCE(?, os_version), model = COALESCE(?, model),"
                    " user_agent = COALESCE(?, user_agent), first_seen_at = COALESCE(first_seen_at, ?),"
                    " last_seen_at = ? WHERE user_id = ? AND device_key = ?",
                    (
                        key,
                        _header(headers, "x-device-os"),
                        _header(headers, "x-ver-os"),
                        _header(headers, "x-device-model"),
                        _header(headers, "user-agent"),
                        timestamp,
                        timestamp,
                        user.user_id,
                        row["device_key"] if row is not None else key,
                    ),
                )
        return result

    def for_user(self, user: UserRecord, privacy: PrivacySettings) -> UserDevices:
        """Private returns count and IDs only; off never reads the device rows."""
        devices: list[ClientDeviceDetails | ClientDevice] = []
        if privacy.device_limits != "off":
            columns = _COLUMNS if privacy.device_limits == "full" else "device_key"
            rows = self._store.rows(
                f"SELECT {columns} FROM devices WHERE user_id = ? ORDER BY device_key", (user.user_id,)
            )
            devices = [
                ClientDeviceDetails.model_validate(dict(row))
                if privacy.device_limits == "full"
                else ClientDevice(device_key=row["device_key"])
                for row in rows
            ]
        return UserDevices(
            username=user.username,
            level=privacy.device_limits,
            count=len(devices),
            limit=effective_device_limit(user.device_limit, privacy.default_device_limit),
            devices=devices,
        )

    def remove(self, user_id: str, device_key: str | None = None) -> DevicesRemoved:
        """Forget one device, or all; a repeated removal is already done (zero rows). Caller owns the transaction."""
        sql = "DELETE FROM devices WHERE user_id = ?"
        params: tuple[str, ...] = (user_id,)
        if device_key is not None:
            sql += " AND device_key = ?"
            params = (*params, device_key)
        return DevicesRemoved(deleted=self._store.db.execute(sql, params).rowcount)

    def lower_level(self, before: DeviceLimitLevel, after: DeviceLimitLevel) -> int:
        """Downgrade device state in the privacy writer's transaction; return deleted rows.

        Full to private re-keys IDs and clears all metadata. Pending private fingerprints
        from a raise to full already have NULL first-seen times and keep their keys.
        """
        if after == "off":
            deleted = self._store.db.execute("DELETE FROM devices").rowcount
            self._store.db.execute("DELETE FROM secrets WHERE name = ?", (DEVICE_HMAC_SECRET,))
            return deleted
        if before == "full" and after == "private":
            rows = self._store.rows("SELECT user_id, device_key, first_seen_at FROM devices")
            self._store.db.execute("DELETE FROM devices")
            for row in rows:
                key = self._fingerprint(row["device_key"]) if row["first_seen_at"] is not None else row["device_key"]
                # Two rows that fingerprint alike are one device; a key collision must never block lowering privacy.
                self._store.db.execute(
                    "INSERT OR IGNORE INTO devices (user_id, device_key) VALUES (?, ?)", (row["user_id"], key)
                )
        return 0

    def _fingerprint(self, hwid: str) -> str:
        key = self._store.secret(DEVICE_HMAC_SECRET)
        if key is None:
            key = secrets.token_bytes(32)
            self._store.set_secret(DEVICE_HMAC_SECRET, key)
        return hmac.new(key, hwid.encode("utf-8"), hashlib.sha256).hexdigest()

    def _count(self, user_id: str) -> int:
        row = self._store.row("SELECT COUNT(*) AS count FROM devices WHERE user_id = ?", (user_id,))
        assert row is not None
        return int(row["count"])
