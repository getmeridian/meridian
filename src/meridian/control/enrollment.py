"""Node enrollment, sessions, and the service's signing identity (`meridian.agent/v1`).

The deployer mints a single-use, short-lived enrollment token over the admin
API and writes it into the node's `AgentBootstrap`. The agent trades it and a
CSR for a node certificate (`enroll`), then opens sessions with a signed
`hello`. Tokens of every kind are stored only as SHA-256; a node's earlier
certificates are revoked when it enrolls again or is deleted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from meridian.control.errors import agent_error
from meridian.control.models import EnrollmentGrant, FleetAuthority
from meridian.control.pki import (
    CertificateError,
    IssuedCertificate,
    check_node_certificate,
    create_ca,
    der_sha256,
    issue_node_certificate,
    issue_service_certificate,
    not_after,
    pem,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp, parse_timestamp
from meridian.core.agent_protocol import (
    Capability,
    EnrollRequest,
    EnrollResponse,
    HelloRequest,
    HelloResponse,
    PollSettings,
    RenewRequest,
    RenewResponse,
)
from meridian.core.agent_signing import (
    SignatureContext,
    certificate_key_id,
    generate_signing_key,
    public_key,
    sign,
    verify_proof,
)
from meridian.core.agent_types import AGENT_PROTOCOL
from meridian.core.version_skew import (
    SUPPORTED_AGENT_PROTOCOLS,
    UNSUPPORTED_PROTOCOL_RETRY_SECONDS,
    common_agent_protocol,
)

ENROLLMENT_TOKEN_SECONDS = 30 * 60
SESSION_SECONDS = 60 * 60
CLOCK_SKEW_SECONDS = 300
# A nonce outlives the window in which its hello could be accepted again.
NONCE_MARGIN_SECONDS = 60
SERVICE_CAPABILITIES: tuple[Capability, ...] = (
    "long_poll",
    "files",
    "systemd_units",
    "xray_config",
    "xray_users",
    "hysteria2_users",
    "binary_pins",
    "traffic_stats",
    "blob_fetch",
)
POLL_SETTINGS = PollSettings(max_wait_seconds=55, report_interval_seconds=60, stats_interval_seconds=60)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    node_id TEXT PRIMARY KEY,
    token_sha256 TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS node_certificates (
    sha256 TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    serial TEXT NOT NULL,
    not_after INTEGER NOT NULL,
    renew_after INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    token_sha256 TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    certificate_sha256 TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hello_nonces (nonce TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
"""


@dataclass(frozen=True)
class Session:
    """An authenticated agent session."""

    node_id: str
    certificate_sha256: str
    expires_at: float


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class NodeAuthority:
    """The fleet CA, the service signing certificate, and every node credential."""

    def __init__(self, store: Store, fleet_id: str, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self.fleet_id = fleet_id
        store.create(_SCHEMA)
        with store.transaction():
            now = self._now()
            signing_key = store.secret("signing_key")
            if signing_key is None:
                signing_key = generate_signing_key()
                store.set_secret("signing_key", signing_key)
            ca_key = store.secret("ca_key")
            ca_der = store.secret("ca_certificate")
            if ca_key is None or ca_der is None:
                ca_key = generate_signing_key()
                ca_der = create_ca(ca_key, now)
                store.set_secret("ca_key", ca_key)
                store.set_secret("ca_certificate", ca_der)
                store.db.execute("DELETE FROM secrets WHERE name = 'service_certificate'")
            service_der = store.secret("service_certificate")
            if service_der is None or not_after(service_der) <= now:
                service_der = issue_service_certificate(ca_key, ca_der, fleet_id, public_key(signing_key), now)
                store.set_secret("service_certificate", service_der)
        self._signing_key = signing_key
        self._ca_key = ca_key
        self._ca_der = ca_der
        self._service_der = service_der
        self.service_key_id = certificate_key_id(service_der)

    # -- identity ---------------------------------------------------------------------

    @property
    def ca_sha256(self) -> str:
        return der_sha256(self._ca_der)

    def fleet_authority(self) -> FleetAuthority:
        return FleetAuthority(ca_sha256=self.ca_sha256, ca_certificate_pem=pem(self._ca_der))

    def service_public_key(self) -> bytes:
        return public_key(self._signing_key)

    def sign(self, document: dict[str, Any], context: SignatureContext) -> dict[str, str]:
        return sign(document, context, self._signing_key, self.service_key_id)

    # -- admin ----------------------------------------------------------------------------

    def grant_enrollment(self, node_id: str) -> EnrollmentGrant:
        """Mint the node's one outstanding enrollment token (an earlier one stops working)."""
        token = secrets.token_urlsafe(32)
        expires_at = int(self._clock()) + ENROLLMENT_TOKEN_SECONDS
        with self._store.transaction():
            self._store.db.execute(
                "INSERT INTO enrollment_tokens VALUES (?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET"
                " token_sha256 = excluded.token_sha256, expires_at = excluded.expires_at",
                (node_id, token_sha256(token), expires_at),
            )
        return EnrollmentGrant(
            node_id=node_id,
            enrollment_token=token,
            token_expires_at=format_timestamp(expires_at),
            ca_sha256=self.ca_sha256,
        )

    def forget_node(self, node_id: str) -> None:
        """Revoke every credential of a node; the caller holds the transaction."""
        self._revoke(node_id)
        self._store.db.execute("DELETE FROM enrollment_tokens WHERE node_id = ?", (node_id,))

    # -- agent ------------------------------------------------------------------------------

    def enroll(self, request: EnrollRequest) -> EnrollResponse:
        if request.fleet_id != self.fleet_id:
            raise agent_error("enrollment_token_invalid", "The enrollment token is invalid or spent.")
        now = self._now()
        with self._store.transaction():
            row = self._store.row(
                "SELECT token_sha256, expires_at FROM enrollment_tokens WHERE node_id = ?", (request.node_id,)
            )
            valid = row is not None and hmac.compare_digest(row["token_sha256"], token_sha256(request.enrollment_token))
            if row is None or not valid or row["expires_at"] <= now.timestamp():
                raise agent_error("enrollment_token_invalid", "The enrollment token is invalid or spent.")
            issued = self._issue(request.node_id, request.csr_pem, now)
            self._store.db.execute("DELETE FROM enrollment_tokens WHERE node_id = ?", (request.node_id,))
            self._revoke(request.node_id, keep=issued.sha256)
        return EnrollResponse(
            fleet_id=self.fleet_id,
            node_id=request.node_id,
            fleet_epoch=self._store.fleet_epoch,
            certificate_pem=issued.pem,
            certificate_expires_at=_stamp(issued.not_after),
            ca_certificate_pem=pem(self._ca_der),
            service_certificate_pem=pem(self._service_der),
        )

    def hello(self, raw: dict[str, Any]) -> tuple[HelloRequest, HelloResponse]:
        """Check a hello and its `NodeProof` (as received) and open a session."""
        # Before validation: a hello from another protocol need not parse as this one's.
        if common_agent_protocol(raw.get("protocols") or []) is None:
            raise agent_error(
                "unsupported_protocol",
                f"This service speaks {', '.join(SUPPORTED_AGENT_PROTOCOLS)}; install a matching agent with "
                "`meridian apply`.",
                retry_after=UNSUPPORTED_PROTOCOL_RETRY_SECONDS,
            )
        request = HelloRequest.model_validate(raw)
        if request.fleet_id != self.fleet_id:
            raise agent_error("unauthenticated", "The hello names another fleet.")
        now = self._now()
        try:
            certificate = check_node_certificate(
                self._ca_der, request.proof.certificate_pem, self.fleet_id, request.node_id, now
            )
        except CertificateError as exc:
            raise agent_error(exc.code, str(exc)) from exc
        issued_at = parse_timestamp(request.proof.issued_at)
        if abs(now.timestamp() - issued_at) > CLOCK_SKEW_SECONDS:
            raise agent_error("clock_skew", "The proof's issued_at is more than 300 seconds off the service clock.")
        if not verify_proof(raw, certificate.public_key):
            raise agent_error("unauthenticated", "The node proof signature does not verify.")
        epoch = self._store.fleet_epoch
        if request.highest_fleet_epoch is not None and request.highest_fleet_epoch > epoch:
            raise agent_error("epoch_superseded", "The agent has accepted a newer fleet epoch than this service's.")
        token = secrets.token_urlsafe(32)
        stamp = int(now.timestamp())
        expires_at = stamp + SESSION_SECONDS
        with self._store.transaction():
            row = self._store.row(
                "SELECT node_id, renew_after, revoked_at FROM node_certificates WHERE sha256 = ?", (certificate.sha256,)
            )
            if row is None or row["node_id"] != request.node_id:
                raise agent_error("unauthenticated", "The node certificate is unknown.")
            if row["revoked_at"] is not None:
                raise agent_error("credential_revoked", "The node certificate is revoked; enroll again over SSH.")
            db = self._store.db
            db.execute("DELETE FROM hello_nonces WHERE expires_at < ?", (stamp,))
            db.execute("DELETE FROM agent_sessions WHERE expires_at < ?", (stamp,))
            if db.execute("SELECT 1 FROM hello_nonces WHERE nonce = ?", (request.proof.nonce,)).fetchone():
                raise agent_error("unauthenticated", "The proof's nonce was already used.")
            nonce_expiry = int(issued_at) + CLOCK_SKEW_SECONDS + NONCE_MARGIN_SECONDS
            db.execute("INSERT INTO hello_nonces VALUES (?, ?)", (request.proof.nonce, nonce_expiry))
            db.execute(
                "INSERT INTO agent_sessions VALUES (?, ?, ?, ?)",
                (token_sha256(token), request.node_id, certificate.sha256, expires_at),
            )
            renew_after = int(row["renew_after"])
        offered = set(request.capabilities)
        return request, HelloResponse(
            protocol=AGENT_PROTOCOL,
            fleet_epoch=epoch,
            capabilities=[name for name in SERVICE_CAPABILITIES if name in offered],
            session_token=token,
            session_expires_at=format_timestamp(expires_at),
            server_time=format_timestamp(stamp),
            poll=POLL_SETTINGS,
            renew_after=format_timestamp(renew_after),
            service_certificate_pem=pem(self._service_der),
        )

    def authenticate(self, token: str) -> Session:
        row = self._store.row(
            "SELECT s.node_id, s.certificate_sha256, s.expires_at, c.revoked_at FROM agent_sessions s"
            " LEFT JOIN node_certificates c ON c.sha256 = s.certificate_sha256 WHERE s.token_sha256 = ?",
            (token_sha256(token),),
        )
        if row is None:
            raise agent_error("unauthenticated", "Unknown session; send hello.")
        if row["revoked_at"] is not None:
            raise agent_error("credential_revoked", "The node certificate is revoked; enroll again over SSH.")
        if row["expires_at"] <= self._clock():
            raise agent_error("credential_expired", "The session expired; send hello.")
        return Session(
            node_id=str(row["node_id"]),
            certificate_sha256=str(row["certificate_sha256"]),
            expires_at=float(row["expires_at"]),
        )

    def renew(self, session: Session, request: RenewRequest) -> RenewResponse:
        """Certify a fresh node key; the session's certificate stays valid until it expires."""
        with self._store.transaction():
            issued = self._issue(session.node_id, request.csr_pem, self._now())
        return RenewResponse(certificate_pem=issued.pem, certificate_expires_at=_stamp(issued.not_after))

    # -- helpers ------------------------------------------------------------------------------

    def _issue(self, node_id: str, csr_pem: str, now: datetime) -> IssuedCertificate:
        try:
            issued = issue_node_certificate(self._ca_key, self._ca_der, self.fleet_id, node_id, csr_pem, now)
        except CertificateError as exc:
            raise agent_error(exc.code, str(exc)) from exc
        self._store.db.execute(
            "INSERT INTO node_certificates VALUES (?, ?, ?, ?, ?, NULL)",
            (
                issued.sha256,
                node_id,
                issued.serial,
                int(issued.not_after.timestamp()),
                int(issued.renew_after.timestamp()),
            ),
        )
        return issued

    def _revoke(self, node_id: str, *, keep: str = "") -> None:
        db = self._store.db
        db.execute(
            "UPDATE node_certificates SET revoked_at = ? WHERE node_id = ? AND revoked_at IS NULL AND sha256 != ?",
            (int(self._clock()), node_id, keep),
        )
        db.execute("DELETE FROM agent_sessions WHERE node_id = ?", (node_id,))

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)


def _stamp(moment: datetime) -> str:
    return format_timestamp(moment.timestamp())
