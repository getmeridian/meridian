"""Scoped API tokens for `/api`: one revocable credential per device or agent.

Every token holds one scope (`read` < `propose` < `approve` < `admin`, each
including the ones before it) and is stored only as SHA-256, with its name,
scope, and times. `last_used_at` is coarse, written at most once a minute, and
nothing records where a token was used from.

The configured admin token (`ServiceConfig.admin_token_sha256`) stays outside
this table as the fleet's root credential: it always holds `admin`, is never
listed, and is rotated by rewriting the config, never revoked over the API, so
the deployer can never be locked out.
"""

from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.models import IssuedToken, TokenCreate, TokenList, TokenRecord, TokenScope, scope_covers
from meridian.control.store import Store
from meridian.control.times import format_timestamp, parse_timestamp

# `mrd_<id>_<secret>`: the public ID finds the row, so the secret's hash is compared once, in constant time.
_TOKEN = re.compile(r"mrd_([0-9a-f]{12})_[0-9a-f]{64}")
LAST_USED_RESOLUTION_SECONDS = 60
# Compared against when no row matches, so an unknown token costs what a known one does.
_NO_HASH = "0" * 64

API_TOKENS_TABLE = """CREATE TABLE IF NOT EXISTS api_tokens (
    token_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
)"""
_COLUMNS = "token_id, name, scope, token_sha256, created_at, last_used_at, revoked_at"


@dataclass(frozen=True)
class Caller:
    """Whom an `/api` request authenticated as: an issued token, or the root credential (`token_id` None)."""

    token_id: str | None
    scope: TokenScope


ROOT = Caller(token_id=None, scope="admin")


def unauthenticated() -> ServiceError:
    """The one refusal for a missing, wrong, or revoked token, so none tells the others apart."""
    return ServiceError("unauthenticated", "Authentication required.", 401)


class TokenRegistry:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        store.create(API_TOKENS_TABLE)

    def authenticate(self, token: str) -> Caller:
        """The issued, unrevoked token `token` names; touches `last_used_at` at most once a minute."""
        match = _TOKEN.fullmatch(token)
        row = self._row(match[1]) if match else None
        expected = str(row["token_sha256"]) if row is not None else _NO_HASH
        if not hmac.compare_digest(token_sha256(token), expected) or row is None or row["revoked_at"] is not None:
            raise unauthenticated()
        now = self._clock()
        last = row["last_used_at"]
        if last is None or now - parse_timestamp(str(last)) >= LAST_USED_RESOLUTION_SECONDS:
            with self._store.transaction():
                self._store.db.execute(
                    "UPDATE api_tokens SET last_used_at = ? WHERE token_id = ?",
                    (format_timestamp(now), row["token_id"]),
                )
        return Caller(token_id=str(row["token_id"]), scope=row["scope"])

    def issue(self, request: TokenCreate, *, issuer: Caller) -> IssuedToken:
        """Mint a token; its secret is in this response only."""
        if not scope_covers(issuer.scope, request.scope):
            raise ServiceError(
                "insufficient_scope", f"A {issuer.scope} token cannot mint a {request.scope} token.", 403
            )
        token_id = secrets.token_hex(6)
        token = f"mrd_{token_id}_{secrets.token_hex(32)}"
        with self._store.transaction():
            self._store.db.execute(
                "INSERT INTO api_tokens (token_id, name, scope, token_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
                (token_id, request.name, request.scope, token_sha256(token), format_timestamp(self._clock())),
            )
        return IssuedToken(**self.get(token_id).model_dump(), token=token)

    def get(self, token_id: str) -> TokenRecord:
        row = self._row(token_id)
        if row is None:
            raise ServiceError("not_found", f"Unknown token {token_id!r}.", 404)
        return _record(row)

    def list_tokens(self) -> TokenList:
        rows = self._store.rows(f"SELECT {_COLUMNS} FROM api_tokens ORDER BY created_at, token_id")
        return TokenList(tokens=[_record(row) for row in rows])

    def revoke(self, token_id: str) -> TokenRecord:
        """Revoke a token from its next request on; revoking it again keeps the first time."""
        with self._store.transaction():
            self.get(token_id)
            self._store.db.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
                (format_timestamp(self._clock()), token_id),
            )
        return self.get(token_id)

    def _row(self, token_id: str) -> sqlite3.Row | None:
        return self._store.row(f"SELECT {_COLUMNS} FROM api_tokens WHERE token_id = ?", (token_id,))


def _record(row: sqlite3.Row) -> TokenRecord:
    return TokenRecord(
        id=str(row["token_id"]),
        name=str(row["name"]),
        scope=row["scope"],
        created_at=str(row["created_at"]),
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )
