"""Bodies of the web UI's sign-in routes (`ui_routes.py`); only the browser speaks them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.agent_types import OpaqueToken
from meridian.core.models import CoreModel

# Long enough for any passphrase the policy accepts; longer bodies are refused before hashing.
_PASSWORD = Field(min_length=1, max_length=1024)
_CODE = Field(default="", max_length=16)


class SetupLinkCheck(CoreModel):
    token: OpaqueToken


class SetupCompletion(CoreModel):
    token: OpaqueToken
    password: str = _PASSWORD


class LoginRequest(CoreModel):
    """The password and, when TOTP is on, the current code; both are checked before either is judged."""

    password: str = _PASSWORD
    code: str = _CODE


class PasswordChange(CoreModel):
    password: str = _PASSWORD


class TotpCode(CoreModel):
    code: str = Field(min_length=1, max_length=16)


class PasskeyState(CoreModel):
    """Passkeys need a domain (a WebAuthn RP ID is never an IP) and a WebAuthn backend, which is not built yet."""

    available: bool = False
    reason: Literal["no_domain", "not_built"]


class AuthState(CoreModel):
    """What the sign-in screen needs before anyone signs in; nothing secret."""

    owner_exists: bool
    totp_enabled: bool
    signed_in: bool
    configured: bool = Field(description="False when the service does not know its secret path, so cannot sign in.")
    passkeys: PasskeyState
    session_expires_at: int | None = None


class SessionInfo(CoreModel):
    id: str
    created_at: int
    last_seen_at: int
    current: bool


class SessionList(CoreModel):
    sessions: list[SessionInfo]


class TotpEnrollment(CoreModel):
    """A pending TOTP secret: shown once, active only after a code from it is confirmed."""

    secret: str
    uri: str
    qr_svg: str = Field(description="The URI as an SVG `data:` URI QR code.")


class ServerView(CoreModel):
    """One agent node as the Servers screen shows it; read-only (topology changes stay in the CLI)."""

    node_id: str
    role: Literal["exit", "gateway", "node"]
    region: str = ""
    live: bool
    last_seen_at: str | None
    agent_version: str | None
    revision: int | None = Field(description="The newest document the service signed for the node.")
    applied_revision: int | None = Field(description="The document the agent last reported applied.")
    in_sync: bool
    failed: list[str]
    errors: list[str]
    traffic_bytes: int = Field(description="Bytes the node counted since its agent's counters began.")


class FleetView(CoreModel):
    """What the UI shows beside the admin API: servers, and who the deployer's fleet file declares."""

    profile_title: str
    public_url: str
    declared_users: list[str] = Field(description="People in the last applied revision's intent (the CLI's).")
    display_names: dict[str, str]
    intent_revision: int | None
    servers: list[ServerView]


class ShareView(CoreModel):
    """How one person connects: their subscription link and QR, and their connection page when one exists."""

    username: str
    declared: bool = Field(
        description="The last applied revision's intent lists them: removing them or issuing a new link is the CLI's."
    )
    subscription_url: str
    page_url: str
    qr_svg: str
