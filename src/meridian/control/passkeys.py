"""Owner passkeys, bounded one-use challenges, and transactional session grants."""

import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from meridian.control.errors import ServiceError
from meridian.control.owner import OwnerAuth, OwnerSession
from meridian.control.passkey_models import (
    AssertionCredential,
    AuthenticationBegin,
    AuthenticationOptions,
    CredentialAlgorithm,
    CredentialDescriptor,
    OwnerOptions,
    PasskeyList,
    PasskeyName,
    PasskeyRecord,
    RegistrationBegin,
    RegistrationCompletion,
    RegistrationOptions,
    RelyingPartyOptions,
)
from meridian.control.passkey_tables import PASSKEY_TABLES
from meridian.control.store import Store
from meridian.control.ui_models import PasskeyState
from meridian.control.webauthn import (
    RelyingParty,
    encode_bytes,
    invalid_passkey,
    relying_party,
    verify_assertion,
    verify_registration,
)

CHALLENGE_SECONDS = 5 * 60
# Anonymous sign-in requests have their own evicting pool; owner kinds are one per live session.
MAX_CHALLENGES = 64
MAX_PASSKEYS = 10
ChallengeKind = Literal["register", "login", "confirm"]


@dataclass(frozen=True)
class Challenge:
    value: bytes
    kind: ChallengeKind
    rp: RelyingParty
    user_handle: bytes
    session_id: str | None
    credential_ids: tuple[str, ...]
    expires_at: float


class PasskeyRegistry:
    def __init__(
        self, store: Store, owner: OwnerAuth, *, public_url: Callable[[], str], clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._owner = owner
        self._public_url = public_url
        self._clock = clock
        self._challenge_lock = threading.Lock()
        self._challenges: dict[str, Challenge] = {}
        for table in PASSKEY_TABLES:
            store.create(table)

    def state(self) -> PasskeyState:
        with self._store.lock:
            rp = relying_party(self._public_url())
            return PasskeyState(
                available=rp is not None,
                reason="ready" if rp is not None else "no_domain",
                registered=rp is not None and bool(self._credentials(rp)),
            )

    def list_passkeys(self) -> PasskeyList:
        rows = self._store.rows(
            "SELECT credential_id, name, rp_id, created_at, last_used_at FROM ui_passkeys"
            " ORDER BY created_at, credential_id"
        )
        return PasskeyList(passkeys=[_record(row) for row in rows])

    def begin_registration(self, session: OwnerSession) -> RegistrationBegin:
        with self._store.transaction():
            self._owner.hold_recent(session)
            rp = self._rp()
            self._hold_registration_room()
            handle = self._user_handle(create=True)
            credentials = self._credentials(rp)
            challenge_id, value = self._challenge("register", rp, handle, credentials, session)
            return RegistrationBegin(
                challenge_id=challenge_id,
                public_key=RegistrationOptions(
                    challenge=encode_bytes(value),
                    rp=RelyingPartyOptions(id=rp.id),
                    user=OwnerOptions(id=encode_bytes(handle)),
                    pub_key_cred_params=[CredentialAlgorithm(alg=-7), CredentialAlgorithm(alg=-8)],
                    exclude_credentials=[CredentialDescriptor(id=item) for item in credentials],
                ),
            )

    def finish_registration(self, session: OwnerSession, request: RegistrationCompletion) -> PasskeyRecord:
        with self._store.transaction():
            self._owner.hold_recent(session)
            self._hold_registration_room()
            challenge = self._take_challenge(request.challenge_id, "register", session)
            self._hold_context(challenge)
            verified = verify_registration(request.credential, challenge=challenge.value, rp=challenge.rp)
            if self._row(verified.credential_id) is not None:
                raise ServiceError("passkey_exists", "This passkey is already saved. Use a different passkey.", 409)
            now = int(self._clock())
            self._store.db.execute(
                "INSERT INTO ui_passkeys (credential_id, public_key, algorithm, sign_count, rp_id, name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    verified.credential_id,
                    verified.public_key,
                    verified.algorithm,
                    verified.sign_count,
                    challenge.rp.id,
                    request.name,
                    now,
                ),
            )
        return PasskeyRecord(id=verified.credential_id, name=request.name, rp_id=challenge.rp.id, created_at=now)

    def begin_authentication(self, *, session: OwnerSession | None = None) -> AuthenticationBegin:
        with self._store.transaction():
            if session is not None:
                self._owner.hold_live(session)
            rp = self._rp()
            credentials = self._credentials(rp)
            if not self._owner.status().exists or not credentials:
                raise ServiceError(
                    "passkey_missing", "No passkey is saved for this console. Sign in with your password.", 409
                )
            challenge_id, value = self._challenge(
                "confirm" if session is not None else "login", rp, self._user_handle(), credentials, session
            )
            return AuthenticationBegin(
                challenge_id=challenge_id,
                public_key=AuthenticationOptions(
                    challenge=encode_bytes(value),
                    rp_id=rp.id,
                    allow_credentials=[CredentialDescriptor(id=item) for item in credentials],
                ),
            )

    def finish_authentication(
        self, challenge_id: str, credential: AssertionCredential, *, session: OwnerSession | None = None
    ) -> OwnerSession:
        with self._store.transaction():
            if session is not None:
                self._owner.hold_live(session)
            challenge = self._take_challenge(challenge_id, "confirm" if session is not None else "login", session)
            self._hold_context(challenge)
            row = self._row(credential.id)
            if row is None or credential.id not in challenge.credential_ids or row["rp_id"] != challenge.rp.id:
                raise invalid_passkey()
            counter = verify_assertion(
                credential,
                challenge=challenge.value,
                rp=challenge.rp,
                public_key=bytes(row["public_key"]),
                algorithm=int(row["algorithm"]),
                sign_count=int(row["sign_count"]),
                user_handle=challenge.user_handle,
            )
            now = int(self._clock())
            self._store.db.execute(
                "UPDATE ui_passkeys SET sign_count = ?, last_used_at = ? WHERE credential_id = ?",
                (counter, now, credential.id),
            )
            if session is not None:
                self._owner.confirm_verified(session)
                return replace(session, confirmed_at=now, token="")
            return self._owner.new_session(now)

    def rename(self, session: OwnerSession, credential_id: str, name: str) -> PasskeyRecord:
        name = PasskeyName(name=name).name
        with self._store.transaction():
            self._owner.hold_recent(session)
            if self._row(credential_id) is None:
                raise ServiceError("not_found", "This passkey has been removed.", 404)
            self._store.db.execute("UPDATE ui_passkeys SET name = ? WHERE credential_id = ?", (name, credential_id))
            row = self._row(credential_id)
            assert row is not None
            return _record(row)

    def remove(self, session: OwnerSession, credential_id: str) -> None:
        with self._store.transaction():
            self._owner.hold_recent(session)
            if self._row(credential_id) is None:
                raise ServiceError("not_found", "This passkey has been removed.", 404)
            self._store.db.execute("DELETE FROM ui_passkeys WHERE credential_id = ?", (credential_id,))
            # A lost key may already have opened a session; retain only the verified browser removing it.
            self._store.db.execute("DELETE FROM ui_sessions WHERE session_id != ?", (session.session_id,))

    def _hold_registration_room(self) -> None:
        if len(self.list_passkeys().passkeys) >= MAX_PASSKEYS:
            raise ServiceError(
                "passkey_limit", "Remove a passkey before adding another; at most ten can be saved.", 409
            )

    def _rp(self) -> RelyingParty:
        rp = relying_party(self._public_url())
        if rp is None:
            raise ServiceError(
                "passkey_no_domain",
                "Passkeys need the console's reviewed HTTPS domain. Use your password and code.",
                409,
            )
        return rp

    def _user_handle(self, *, create: bool = False) -> bytes:
        row = self._store.row("SELECT user_handle FROM ui_passkey_owner WHERE id = 1")
        if row is not None:
            return bytes(row[0])
        if not create:
            raise invalid_passkey()
        value = secrets.token_bytes(32)
        self._store.db.execute("INSERT INTO ui_passkey_owner (id, user_handle) VALUES (1, ?)", (value,))
        return value

    def _credentials(self, rp: RelyingParty) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in self._store.rows(
                "SELECT credential_id FROM ui_passkeys WHERE rp_id = ? ORDER BY credential_id", (rp.id,)
            )
        )

    def _row(self, credential_id: str) -> sqlite3.Row | None:
        return self._store.row("SELECT * FROM ui_passkeys WHERE credential_id = ?", (credential_id,))

    def _hold_context(self, challenge: Challenge) -> None:
        if (
            self._rp() != challenge.rp
            or self._user_handle() != challenge.user_handle
            or not self._owner.status().exists
        ):
            raise invalid_passkey()

    def _challenge(
        self,
        kind: ChallengeKind,
        rp: RelyingParty,
        handle: bytes,
        credentials: tuple[str, ...],
        session: OwnerSession | None,
    ) -> tuple[str, bytes]:
        session_id = session.session_id if session is not None else None
        live_sessions = {item.id for item in self._owner.list_sessions(session).sessions} if session else None
        with self._challenge_lock:
            now = self._clock()
            self._challenges = {
                key: item
                for key, item in self._challenges.items()
                if item.expires_at > now
                and (live_sessions is None or item.session_id is None or item.session_id in live_sessions)
                and not (session_id is not None and item.session_id == session_id and item.kind == kind)
            }
            if session_id is None:
                anonymous = [key for key, item in self._challenges.items() if item.session_id is None]
                for key in anonymous[: max(0, len(anonymous) - MAX_CHALLENGES + 1)]:
                    del self._challenges[key]
            challenge_id, value = secrets.token_urlsafe(32), secrets.token_bytes(32)
            self._challenges[challenge_id] = Challenge(
                value, kind, rp, handle, session_id, credentials, now + CHALLENGE_SECONDS
            )
            return challenge_id, value

    def _take_challenge(self, challenge_id: str, kind: ChallengeKind, session: OwnerSession | None) -> Challenge:
        with self._challenge_lock:
            challenge = self._challenges.pop(challenge_id, None)
        if (
            challenge is None
            or challenge.expires_at <= self._clock()
            or challenge.kind != kind
            or challenge.session_id != (session.session_id if session is not None else None)
        ):
            raise ServiceError("passkey_expired", "This passkey request expired or was already used. Start again.", 410)
        return challenge


def _record(row: sqlite3.Row) -> PasskeyRecord:
    return PasskeyRecord(
        id=str(row["credential_id"]),
        name=str(row["name"]),
        rp_id=str(row["rp_id"]),
        created_at=int(row["created_at"]),
        last_used_at=row["last_used_at"],
    )
