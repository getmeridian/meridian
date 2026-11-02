"""Scoped API tokens for `/api`: one revocable credential per device or agent.

Every token holds a frozen capability grant, explicitly selected or expanded
once from a compatible scope preset, and is stored only as SHA-256 with its
name, grant, and times. `last_used_at` is coarse, written at most once a minute, and
nothing records where a token was used from.

The configured admin token (`ServiceConfig.admin_token_sha256`) stays outside
this table as the fleet's root credential: it always holds `admin`, is never
listed, and is rotated by rewriting the config, never revoked over the API, so
the deployer can never be locked out.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import (
    IssuedToken,
    RevisionActor,
    TokenCreate,
    TokenList,
    TokenRecord,
    TokenScope,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp, parse_timestamp
from meridian.core.fleet_events import TokenEventDetail
from meridian.core.token_models import TOKEN_CAPABILITIES, CapabilityGrant, Permission, TokenCapability, TokenPreview

# `mrd_<id>_<secret>`: the public ID finds the row, so the secret's hash is compared once, in constant time.
_TOKEN = re.compile(r"mrd_([0-9a-f]{12})_[0-9a-f]{64}")
LAST_USED_RESOLUTION_SECONDS = 60
# Compared against when no row matches, so an unknown token costs what a known one does.
_NO_HASH = "0" * 64

API_TOKENS_TABLE_V4 = """CREATE TABLE IF NOT EXISTS api_tokens (
    token_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
)"""
# Keep step 4's table unchanged: older databases still pass through step 12's ALTER.
API_TOKENS_TABLE = API_TOKENS_TABLE_V4.removesuffix("\n)") + ",\n    expires_at TEXT,\n    capabilities TEXT\n)"
_COLUMNS = "token_id, name, scope, token_sha256, created_at, last_used_at, revoked_at, expires_at, capabilities"


@dataclass(frozen=True)
class Caller:
    """Whom an `/api` request authenticated as: an issued token, the root credential, or the web owner.

    `token_id` is None for the root credential and the web owner's session; `name`
    tells them apart (`None` is the root, the deployer's CLI).
    """

    token_id: str | None
    scope: TokenScope | None
    name: str | None = None
    grant: CapabilityGrant | None = None

    @property
    def capabilities(self) -> frozenset[TokenCapability]:
        if self.grant is not None:
            return self.grant.capabilities
        if self == ROOT or self == WEB_OWNER:
            # These identities are not issued presets; owner routes and delegation still have their own bounds.
            return frozenset(TOKEN_CAPABILITIES)
        from meridian.control.token_capabilities import legacy_capabilities

        return legacy_capabilities(self.scope) if self.scope is not None else frozenset()

    def allows(self, capability: Permission) -> bool:
        return self == ROOT if capability == "root" else capability in self.capabilities


ROOT = Caller(token_id=None, scope="admin")
# A signed-in web UI owner holds the admin scope (`ui_routes.py`); revisions it records name it `owner`.
WEB_OWNER = Caller(token_id=None, scope="admin", name="owner")


def unauthenticated() -> ServiceError:
    """The one refusal for a missing, wrong, revoked, or expired token."""
    return ServiceError("unauthenticated", "Authentication required.", 401)


class TokenRegistry:
    def __init__(
        self, store: Store, *, clock: Callable[[], float] = time.time, events: EventLedger | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        store.create(API_TOKENS_TABLE)
        self._events = events if events is not None else EventLedger(store, clock=clock)
        self._freeze_legacy_grants()

    def _freeze_legacy_grants(self) -> None:
        from meridian.control.token_capabilities import legacy_capabilities
        from meridian.core.token_models import TOKEN_SCOPES

        with self._store.transaction():
            for row in self._store.rows("SELECT token_id, scope FROM api_tokens WHERE capabilities IS NULL"):
                if row["scope"] not in TOKEN_SCOPES:
                    raise ServiceError("conflict", "A stored token has invalid permissions.", 409)
                self._store.db.execute(
                    "UPDATE api_tokens SET capabilities = ? WHERE token_id = ?",
                    (json.dumps(sorted(legacy_capabilities(row["scope"]))), row["token_id"]),
                )

    def actor(self, caller: Caller) -> RevisionActor:
        """The durable author of a change: the token's name, the web owner, or root."""
        name = caller.name or "root" if caller.token_id is None else self.get(caller.token_id).name
        return RevisionActor(token_id=caller.token_id, name=name)

    def authenticate(self, token: str) -> Caller:
        """The issued, unrevoked, unexpired token; touches `last_used_at` at most once a minute."""
        match = _TOKEN.fullmatch(token)
        row = self._row(match[1]) if match else None
        expected = str(row["token_sha256"]) if row is not None else _NO_HASH
        now = self._clock()
        if (
            not hmac.compare_digest(token_sha256(token), expected)
            or row is None
            or row["revoked_at"] is not None
            or (row["expires_at"] is not None and datetime.fromisoformat(row["expires_at"]).timestamp() <= now)
        ):
            raise unauthenticated()
        grant = _grant(row)
        last = row["last_used_at"]
        if last is None or now - parse_timestamp(str(last)) >= LAST_USED_RESOLUTION_SECONDS:
            with self._store.transaction():
                self._store.db.execute(
                    "UPDATE api_tokens SET last_used_at = ? WHERE token_id = ?",
                    (format_timestamp(now), row["token_id"]),
                )
        return Caller(
            token_id=str(row["token_id"]), scope=None if row["scope"] == "custom" else row["scope"], grant=grant
        )

    def preview(self, request: TokenCreate, *, issuer: Caller) -> TokenPreview:
        """Review effective permissions and expiry under the lock; create no row, secret, or event."""
        from meridian.control.token_capabilities import console_capabilities, permission, requested_capabilities

        with self._store.lock:
            capabilities = requested_capabilities(request)
            if not capabilities <= issuer.capabilities:
                raise ServiceError("insufficient_scope", "A token cannot grant permissions it does not hold.", 403)
            if (
                issuer.token_id is None
                and issuer.name == "owner"
                and (request.scope == "admin" or not capabilities <= console_capabilities())
            ):
                raise ServiceError(
                    "insufficient_scope", "Use the CLI to issue fleet administration or backup permissions.", 403
                )
            now = self._clock()
            expires_at = request.expires_at
            if request.expires_in_days is not None:
                expires_at = format_timestamp(now + request.expires_in_days * 86400)
            if expires_at is not None and datetime.fromisoformat(expires_at).timestamp() <= now:
                raise ServiceError("invalid_request", "Token expiry must be in the future.", 400)
            if issuer.token_id is not None:
                parent = self._row(issuer.token_id)
                if (
                    parent is None
                    or parent["revoked_at"] is not None
                    or (
                        parent["expires_at"] is not None
                        and datetime.fromisoformat(parent["expires_at"]).timestamp() <= now
                    )
                ):
                    raise unauthenticated()
                if not capabilities <= _grant(parent).capabilities:
                    raise ServiceError(
                        "insufficient_scope", "The issuing token no longer holds these permissions.", 403
                    )
                if parent["expires_at"] is not None and (
                    expires_at is None
                    or datetime.fromisoformat(expires_at).timestamp()
                    > datetime.fromisoformat(parent["expires_at"]).timestamp()
                ):
                    raise ServiceError("insufficient_scope", "Token expiry cannot outlast the issuing token.", 403)
            return TokenPreview(
                scope=request.scope,
                capabilities=sorted(capabilities),
                permissions=[permission(capability) for capability in sorted(capabilities)],
                expires_at=expires_at,
            )

    def issue(
        self, request: TokenCreate, *, issuer: Caller, still_allowed: Callable[[], None] | None = None
    ) -> IssuedToken:
        """Mint a token; its secret is in this response only.

        `still_allowed` runs inside the insert's transaction and raises to refuse, for a
        caller whose right to mint can lapse while the request waits (a console session).
        """
        self.preview(request, issuer=issuer)
        token_id = secrets.token_hex(6)
        token = f"mrd_{token_id}_{secrets.token_hex(32)}"
        with self._store.transaction():
            if still_allowed is not None:
                still_allowed()
            preview = self.preview(request, issuer=issuer)
            now = self._clock()
            created_at = format_timestamp(now)
            self._store.db.execute(
                "INSERT INTO api_tokens (token_id, name, scope, token_sha256, created_at, expires_at, capabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token_id,
                    request.name,
                    request.scope or "custom",
                    token_sha256(token),
                    created_at,
                    preview.expires_at,
                    json.dumps(preview.capabilities),
                ),
            )
            self._events.append(
                "token.created",
                request.name,
                actor=self.actor(issuer).name,
                now=created_at,
                detail=TokenEventDetail(name=request.name, scope=request.scope, capabilities=preview.capabilities),
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

    def revoke(self, token_id: str, *, caller: Caller = ROOT) -> TokenRecord:
        """Revoke a token from its next request on; revoking it again keeps the first time."""
        with self._store.transaction():
            record = self.get(token_id)
            if record.revoked_at is not None:
                return record
            now = format_timestamp(self._clock())
            self._store.db.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
                (now, token_id),
            )
            self._events.append(
                "token.revoked",
                record.name,
                actor=self.actor(caller).name,
                now=now,
                detail=TokenEventDetail(name=record.name, scope=record.scope, capabilities=record.capabilities),
            )
        return self.get(token_id)

    def _row(self, token_id: str) -> sqlite3.Row | None:
        return self._store.row(f"SELECT {_COLUMNS} FROM api_tokens WHERE token_id = ?", (token_id,))


def _record(row: sqlite3.Row) -> TokenRecord:
    return TokenRecord(
        id=str(row["token_id"]),
        name=str(row["name"]),
        scope=None if row["scope"] == "custom" else row["scope"],
        capabilities=sorted(_grant(row).capabilities),
        created_at=str(row["created_at"]),
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        expires_at=row["expires_at"],
    )


def _grant(row: sqlite3.Row) -> CapabilityGrant:
    try:
        return CapabilityGrant.model_validate_json('{"capabilities":' + str(row["capabilities"]) + "}")
    except ValueError as exc:
        raise unauthenticated() from exc
