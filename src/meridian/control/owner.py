"""The web UI's one owner: setup links, password, optional TOTP, and browser sessions.

- **Setup links** — the root admin token (the deployer's CLI) issues a single-use
  token valid for 15 minutes; issuing one voids any earlier unused link. A `setup`
  link works only while no owner exists; a `reset` link (`meridian panel
  --setup-link`) also replaces an owner's password, turns TOTP off, and ends every
  session, so whoever holds the deployer's machine can always recover.
- **Sessions** — a random token in an `HttpOnly` cookie; only its SHA-256 is
  stored. A session ends after `IDLE_SECONDS` without use or `ABSOLUTE_SECONDS`
  after sign-in, whichever comes first. Sensitive changes need the password (and
  code) again within `CONFIRM_SECONDS` (`require_recent`).
- **No addresses** — sessions keep only times; guess limits live in memory
  (`login_limits.py`).

Times in these tables are integer epoch seconds (session arithmetic, not protocol
timestamps). Every table is `ui_*`: snapshots must leave them out, and a restored
service starts without an owner (`meridian panel --setup-link`).
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.login_limits import LoginLimiter
from meridian.control.models import OwnerStatus, SetupLinkGrant
from meridian.control.passwords import (
    PasswordPolicyError,
    check_password_policy,
    hash_password,
    new_totp_secret,
    otpauth_uri,
    totp_match,
    verify_password,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.control.tokens import unauthenticated
from meridian.control.ui_models import SessionInfo, SessionList

SETUP_LINK_SECONDS = 15 * 60
IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 12 * 60 * 60
CONFIRM_SECONDS = 5 * 60
TOUCH_RESOLUTION_SECONDS = 60
MAX_SESSIONS = 10
OWNER_ACCOUNT = "owner"
SETUP_ACCOUNT = "setup-link"
TOTP_ISSUER = "Meridian"

UI_OWNER_TABLE = """CREATE TABLE IF NOT EXISTS ui_owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL,
    totp_secret TEXT,
    totp_pending TEXT,
    totp_last_step INTEGER
)"""
UI_SESSIONS_TABLE = """CREATE TABLE IF NOT EXISTS ui_sessions (
    session_id TEXT PRIMARY KEY,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    confirmed_at INTEGER NOT NULL
)"""
UI_SETUP_LINKS_TABLE = """CREATE TABLE IF NOT EXISTS ui_setup_links (
    token_sha256 TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
)"""
UI_TABLES = (UI_OWNER_TABLE, UI_SESSIONS_TABLE, UI_SETUP_LINKS_TABLE)


@dataclass(frozen=True)
class OwnerSession:
    """A signed-in browser: `token` is set only when the session was just created (it goes in the cookie)."""

    session_id: str
    created_at: int
    confirmed_at: int
    token: str = ""

    @property
    def expires_at(self) -> int:
        return self.created_at + ABSOLUTE_SECONDS


def wrong_credentials() -> ServiceError:
    """One refusal for a wrong password, a wrong code, or both, so none is told apart."""
    return ServiceError("wrong_credentials", "Wrong password or code.", 401)


class OwnerAuth:
    def __init__(
        self, store: Store, *, clock: Callable[[], float] = time.time, limiter: LoginLimiter | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        self.limiter = limiter or LoginLimiter()
        for table in UI_TABLES:
            store.create(table)

    # -- owner -----------------------------------------------------------------------

    def status(self) -> OwnerStatus:
        row = self._owner()
        return OwnerStatus(exists=row is not None, totp_enabled=bool(row and row["totp_secret"]))

    # -- setup links -------------------------------------------------------------------

    def issue_setup_link(self, *, reset: bool) -> SetupLinkGrant:
        """Mint a setup (or, with `reset`, recovery) link and void every earlier unused one."""
        now = self._now()
        token = secrets.token_urlsafe(32)
        with self._store.transaction():
            exists = self._owner() is not None
            if exists and not reset:
                raise ServiceError(
                    "owner_exists", "The web UI already has an owner; issue a reset link to replace the password.", 409
                )
            purpose: Literal["setup", "reset"] = "reset" if exists else "setup"
            self._store.db.execute("DELETE FROM ui_setup_links WHERE used_at IS NULL OR expires_at < ?", (now,))
            self._store.db.execute(
                "INSERT INTO ui_setup_links (token_sha256, purpose, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_sha256(token), purpose, now, now + SETUP_LINK_SECONDS),
            )
        return SetupLinkGrant(token=token, purpose=purpose, expires_at=format_timestamp(now + SETUP_LINK_SECONDS))

    def check_setup_link(self, token: str, *, source: str) -> str:
        """The purpose of a live link, without spending it."""
        self.limiter.check(SETUP_ACCOUNT, source)
        row = self._live_link(token)
        if row is None:
            self.limiter.failed(SETUP_ACCOUNT, source)
            raise _dead_link()
        return str(row["purpose"])

    def complete_setup(self, token: str, password: str, *, source: str) -> OwnerSession:
        """Spend the link: set the password (a reset also turns TOTP off and ends every session) and sign in."""
        self.check_setup_link(token, source=source)  # cheap refusal before the slow hash
        try:
            check_password_policy(password)
        except PasswordPolicyError as exc:
            raise ServiceError("weak_password", str(exc), 400) from exc
        password_hash = hash_password(password)  # slow; never under the store lock
        now = self._now()
        with self._store.transaction():
            row = self._live_link(token)
            if row is None:
                self.limiter.failed(SETUP_ACCOUNT, source)
                raise _dead_link()
            exists = self._owner() is not None
            if row["purpose"] == "setup" and exists:
                raise ServiceError("owner_exists", "The web UI already has an owner.", 409)
            self._store.db.execute("UPDATE ui_setup_links SET used_at = ? WHERE token_sha256 = ?", (now, row[0]))
            self._store.db.execute(
                "INSERT INTO ui_owner (id, password_hash, created_at, password_changed_at) VALUES (1, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET password_hash = excluded.password_hash,"
                " password_changed_at = excluded.password_changed_at,"
                " totp_secret = NULL, totp_pending = NULL, totp_last_step = NULL",
                (password_hash, now, now),
            )
            self._store.db.execute("DELETE FROM ui_sessions")
            session = self._new_session(now)
        self.limiter.succeeded(SETUP_ACCOUNT, source)
        return session

    # -- sign-in -----------------------------------------------------------------------

    def login(self, password: str, code: str, *, source: str) -> OwnerSession:
        self._verify(password, code, source=source)
        with self._store.transaction():
            return self._new_session(self._now())

    def confirm(self, session: OwnerSession, password: str, code: str, *, source: str) -> None:
        """Prove the owner is still at the keyboard; sensitive changes need it within `CONFIRM_SECONDS`."""
        self._verify(password, code, source=source)
        with self._store.transaction():
            self._store.db.execute(
                "UPDATE ui_sessions SET confirmed_at = ? WHERE session_id = ?", (self._now(), session.session_id)
            )

    def _verify(self, password: str, code: str, *, source: str) -> None:
        """Check password and code together; any failure counts once and reads the same."""
        self.limiter.check(OWNER_ACCOUNT, source)
        row = self._owner()
        if row is None:
            raise ServiceError("setup_required", "The web UI has no owner yet; run `meridian panel`.", 409)
        password_ok = verify_password(password, str(row["password_hash"]))
        with self._store.transaction():
            owner = self._owner()
            secret = owner["totp_secret"] if owner is not None else None
            step = None
            if owner is not None and secret:
                step = totp_match(str(secret), code, now=self._clock(), last_step=owner["totp_last_step"])
            if not password_ok or (secret and step is None):
                self.limiter.failed(OWNER_ACCOUNT, source)
                raise wrong_credentials()
            if step is not None:
                self._store.db.execute("UPDATE ui_owner SET totp_last_step = ? WHERE id = 1", (step,))
        self.limiter.succeeded(OWNER_ACCOUNT, source)

    # -- sessions ----------------------------------------------------------------------

    def authenticate(self, token: str) -> OwnerSession:
        """The live session `token` opens; touches it at most once a minute. 401 otherwise."""
        if not token:
            raise unauthenticated()
        now = self._now()
        digest = token_sha256(token)
        with self._store.transaction():
            row = self._store.row(
                "SELECT session_id, created_at, last_seen_at, confirmed_at FROM ui_sessions WHERE token_sha256 = ?",
                (digest,),
            )
            if row is None:
                raise unauthenticated()
            if now - int(row["last_seen_at"]) >= IDLE_SECONDS or now - int(row["created_at"]) >= ABSOLUTE_SECONDS:
                self._store.db.execute("DELETE FROM ui_sessions WHERE session_id = ?", (row["session_id"],))
                raise unauthenticated()
            if now - int(row["last_seen_at"]) >= TOUCH_RESOLUTION_SECONDS:
                self._store.db.execute(
                    "UPDATE ui_sessions SET last_seen_at = ? WHERE session_id = ?", (now, row["session_id"])
                )
        return OwnerSession(
            session_id=str(row["session_id"]),
            created_at=int(row["created_at"]),
            confirmed_at=int(row["confirmed_at"]),
        )

    def logout(self, session: OwnerSession) -> None:
        with self._store.transaction():
            self._store.db.execute("DELETE FROM ui_sessions WHERE session_id = ?", (session.session_id,))

    def list_sessions(self, current: OwnerSession) -> SessionList:
        rows = self._store.rows(
            "SELECT session_id, created_at, last_seen_at FROM ui_sessions WHERE last_seen_at > ? AND created_at > ?"
            " ORDER BY created_at DESC",
            (self._now() - IDLE_SECONDS, self._now() - ABSOLUTE_SECONDS),
        )
        return SessionList(
            sessions=[
                SessionInfo(
                    id=str(row["session_id"]),
                    created_at=int(row["created_at"]),
                    last_seen_at=int(row["last_seen_at"]),
                    current=row["session_id"] == current.session_id,
                )
                for row in rows
            ]
        )

    def revoke_others(self, current: OwnerSession) -> int:
        with self._store.transaction():
            cursor = self._store.db.execute("DELETE FROM ui_sessions WHERE session_id != ?", (current.session_id,))
        return int(cursor.rowcount)

    def require_recent(self, session: OwnerSession) -> None:
        """Refuse a sensitive change unless the owner confirmed their password within `CONFIRM_SECONDS`."""
        if self._now() - session.confirmed_at > CONFIRM_SECONDS:
            raise ServiceError("reauth_required", "Confirm your password to continue.", 403)

    # -- password and TOTP -------------------------------------------------------------

    def change_password(self, session: OwnerSession, password: str) -> None:
        """Replace the password and end every other session."""
        self.require_recent(session)
        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            raise ServiceError("weak_password", str(exc), 400) from exc
        now = self._now()
        with self._store.transaction():
            self._store.db.execute(
                "UPDATE ui_owner SET password_hash = ?, password_changed_at = ? WHERE id = 1", (password_hash, now)
            )
            self._store.db.execute("DELETE FROM ui_sessions WHERE session_id != ?", (session.session_id,))

    def begin_totp(self, session: OwnerSession, *, account: str) -> tuple[str, str]:
        """Store a pending secret and return it with its `otpauth://` URI; nothing changes until it is confirmed."""
        self.require_recent(session)
        secret = new_totp_secret()
        with self._store.transaction():
            if self._owner() is None:
                raise ServiceError("setup_required", "The web UI has no owner yet.", 409)
            self._store.db.execute("UPDATE ui_owner SET totp_pending = ? WHERE id = 1", (secret,))
        return secret, otpauth_uri(secret, account=account, issuer=TOTP_ISSUER)

    def enable_totp(self, session: OwnerSession, code: str, *, source: str) -> None:
        """Turn TOTP on once a code from the pending secret matches; other sessions end."""
        self.require_recent(session)
        self.limiter.check(OWNER_ACCOUNT, source)
        with self._store.transaction():
            owner = self._owner()
            pending = owner["totp_pending"] if owner is not None else None
            if not pending:
                raise ServiceError("conflict", "Start setting up the authenticator again.", 409)
            step = totp_match(str(pending), code, now=self._clock(), last_step=None)
            if step is None:
                self.limiter.failed(OWNER_ACCOUNT, source)
                raise ServiceError("wrong_code", "That code does not match. Check the device's clock.", 400)
            self._store.db.execute(
                "UPDATE ui_owner SET totp_secret = totp_pending, totp_pending = NULL, totp_last_step = ? WHERE id = 1",
                (step,),
            )
            self._store.db.execute("DELETE FROM ui_sessions WHERE session_id != ?", (session.session_id,))
        self.limiter.succeeded(OWNER_ACCOUNT, source)

    def disable_totp(self, session: OwnerSession) -> None:
        self.require_recent(session)
        with self._store.transaction():
            self._store.db.execute(
                "UPDATE ui_owner SET totp_secret = NULL, totp_pending = NULL, totp_last_step = NULL WHERE id = 1"
            )

    # -- helpers -----------------------------------------------------------------------

    def _new_session(self, now: int) -> OwnerSession:
        """Insert a session (inside a transaction), dropping expired ones and the least recent beyond the cap."""
        db = self._store.db
        db.execute(
            "DELETE FROM ui_sessions WHERE last_seen_at <= ? OR created_at <= ?",
            (now - IDLE_SECONDS, now - ABSOLUTE_SECONDS),
        )
        db.execute(
            "DELETE FROM ui_sessions WHERE session_id IN (SELECT session_id FROM ui_sessions"
            " ORDER BY last_seen_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
            (MAX_SESSIONS - 1,),
        )
        token = secrets.token_urlsafe(32)
        session_id = secrets.token_hex(8)
        db.execute(
            "INSERT INTO ui_sessions (session_id, token_sha256, created_at, last_seen_at, confirmed_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, token_sha256(token), now, now, now),
        )
        return OwnerSession(session_id=session_id, created_at=now, confirmed_at=now, token=token)

    def _live_link(self, token: str) -> sqlite3.Row | None:
        return self._store.row(
            "SELECT token_sha256, purpose FROM ui_setup_links WHERE token_sha256 = ? AND used_at IS NULL"
            " AND expires_at > ?",
            (token_sha256(token), self._now()),
        )

    def _owner(self) -> sqlite3.Row | None:
        return self._store.row("SELECT * FROM ui_owner WHERE id = 1")

    def _now(self) -> int:
        return int(self._clock())


def _dead_link() -> ServiceError:
    return ServiceError("link_invalid", "This setup link has expired or was already used.", 410)
