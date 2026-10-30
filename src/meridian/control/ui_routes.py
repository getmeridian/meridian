"""The web UI's routes: static files, the owner's sign-in, and the UI's read-only views.

`server.py` dispatches these beside `/api`. The page (`/`) and its files (`/ui/...`)
are public: they hold no data. Everything else needs the owner's session cookie or
an API token, through the same `/api` scope checks (`server._Handler._authorize`
maps a session to the admin scope).

Cookie and CSRF rules:
- the session cookie is `__Secure-`, `HttpOnly`, `SameSite=Strict`, scoped to the
  secret path (`ServiceConfig.public_path`); without that path sign-in stays closed;
- every request that changes something with a session, and every sign-in request,
  must carry `X-Meridian-Request: 1` (a cross-site page cannot send it without a
  CORS preflight, which the service never answers), and a browser's
  `Sec-Fetch-Site`/`Origin` must say same-origin.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from meridian.control.errors import ServiceError
from meridian.control.models import USERNAME_PATTERN, OwnerStatus, SetupLinkGrant, SetupLinkRequest, TokenScope
from meridian.control.owner import ABSOLUTE_SECONDS, OwnerSession
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT, Caller
from meridian.control.ui_models import (
    AuthState,
    LoginRequest,
    PasskeyState,
    PasswordChange,
    SetupCompletion,
    SetupLinkCheck,
    TotpCode,
    TotpEnrollment,
)
from meridian.control.ui_views import fleet_view, qr_data_uri, share_view

UI_DIR = Path(__file__).parent / "ui"
SESSION_COOKIE = "__Secure-mrd_owner"
CSRF_HEADER = "X-Meridian-Request"
# The page runs only its own files: no inline script or style, no frames, no outside requests.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
_ASSET = re.compile(r"(?:locales/)?[a-z0-9-]+\.(?:js|css|svg|json)")
_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json"}
_IP_HOST = re.compile(r"^\[?[0-9a-fA-F:.]+\]?$")


@dataclass
class UiContext:
    service: ControlService
    public_path: str = ""
    _assets: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)

    @property
    def cookie_path(self) -> str:
        return f"{self.public_path}/"

    def page(self) -> bytes:
        if "" not in self._assets:
            self._assets[""] = ((UI_DIR / "index.html").read_bytes(), "text/html", "")
        return self._assets[""][0]

    def asset(self, name: str) -> tuple[bytes, str, str] | None:
        """(bytes, content type, ETag) of a shipped UI file, read once."""
        if name not in self._assets:
            path = UI_DIR / name
            if not _ASSET.fullmatch(name) or not path.is_file():
                return None
            data = path.read_bytes()
            content_type = _TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._assets[name] = (data, content_type, '"' + hashlib.sha256(data).hexdigest()[:32] + '"')
        return self._assets[name]


class UiRequest(Protocol):
    """What these handlers need from the HTTP handler (`server._Handler` provides it)."""

    command: str
    headers: Message
    caller: Caller | None
    owner_session: OwnerSession | None

    @property
    def ui(self) -> UiContext: ...

    def read_body(self) -> bytes: ...

    def respond(self, status: int, content_type: str, data: bytes, headers: dict[str, str] | None = None) -> None: ...


# -- request checks ------------------------------------------------------------------------


def require_same_origin(request: UiRequest) -> None:
    """Refuse a sign-in or session write that a cross-site page could have sent."""
    headers = request.headers
    if headers.get(CSRF_HEADER) != "1":
        raise ServiceError("csrf", "The request is missing the UI's request header.", 403)
    site = headers.get("Sec-Fetch-Site")
    if site is not None and site != "same-origin":
        raise ServiceError("csrf", "Cross-site requests are refused.", 403)
    origin = headers.get("Origin")
    if origin is not None and _hostname(urlsplit(origin).netloc) != _hostname(headers.get("Host", "")):
        raise ServiceError("csrf", "Cross-origin requests are refused.", 403)


def session_token(headers: Message) -> str:
    for part in headers.get("Cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            return value.strip()
    return ""


def passive_read(request: UiRequest) -> bool:
    """Only read requests can opt out of renewing the owner's idle activity."""
    return request.command in ("GET", "HEAD") and request.headers.get("X-Meridian-Passive") == "1"


def owner_session(request: UiRequest) -> OwnerSession:
    """The signed-in owner's session (401 otherwise); writes also pass `require_same_origin`."""
    if request.command not in ("GET", "HEAD"):
        require_same_origin(request)
    session = request.ui.service.owner.authenticate(session_token(request.headers), touch=not passive_read(request))
    request.owner_session = session
    return session


def passkey_state(headers: Message) -> PasskeyState:
    """Passkeys need a domain: a WebAuthn RP ID can never be an IP address."""
    host = _hostname(headers.get("Host", ""))
    if not host or _IP_HOST.match(host) or "." not in host:
        return PasskeyState(reason="no_domain")
    return PasskeyState(reason="not_built")


def _hostname(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0].lower()


# -- responses -------------------------------------------------------------------------------


def _send_json(request: UiRequest, body: BaseModel | dict[str, Any], *, cookie: str | None = None) -> None:
    data = (body.model_dump_json() if isinstance(body, BaseModel) else json.dumps(body, ensure_ascii=False)).encode(
        "utf-8"
    )
    headers = dict(SECURITY_HEADERS)
    if cookie is not None:
        headers["Set-Cookie"] = cookie
    request.respond(200, "application/json", data, headers)


def _session_cookie(ui: UiContext, token: str) -> str:
    return (
        f"{SESSION_COOKIE}={token}; Path={ui.cookie_path}; Max-Age={ABSOLUTE_SECONDS}; Secure; HttpOnly; "
        "SameSite=Strict"
    )


def _cleared_cookie(ui: UiContext) -> str:
    return f"{SESSION_COOKIE}=; Path={ui.cookie_path}; Max-Age=0; Secure; HttpOnly; SameSite=Strict"


def _body(request: UiRequest, model: type[Any]) -> Any:
    return model.model_validate_json(request.read_body())


def _require_configured(ui: UiContext) -> None:
    if not ui.public_path:
        raise ServiceError(
            "ui_unconfigured", "This control service does not know its path yet; run `meridian apply`.", 503
        )


# -- static files ------------------------------------------------------------------------------


def page(request: UiRequest, _match: re.Match[str]) -> None:
    """The shell; `no-store` like every response but the static files, so no copy outlives a sign-out."""
    request.respond(200, "text/html; charset=utf-8", request.ui.page(), dict(SECURITY_HEADERS))


def asset(request: UiRequest, match: re.Match[str]) -> None:
    found = request.ui.asset(match[1])
    if found is None:
        raise ServiceError("not_found", "Not found.", 404)
    data, content_type, etag = found
    # Static files hold no secrets: revalidate by ETag (nginx's gzip turns it weak, `W/`).
    headers = {**SECURITY_HEADERS, "ETag": etag, "Cache-Control": "no-cache"}
    candidates = {tag.strip().removeprefix("W/") for tag in request.headers.get("If-None-Match", "").split(",")}
    if etag in candidates:
        request.respond(304, content_type, b"", headers)
        return
    request.respond(200, content_type, data, headers)


# -- sign-in ----------------------------------------------------------------------------------


def auth_state(request: UiRequest, _match: re.Match[str]) -> None:
    ui = request.ui
    status = ui.service.owner.status()
    session: OwnerSession | None = None
    token = session_token(request.headers)
    if token:
        try:
            session = ui.service.owner.authenticate(token, touch=not passive_read(request))
        except ServiceError:
            session = None
    _send_json(
        request,
        AuthState(
            owner_exists=status.exists,
            totp_enabled=status.totp_enabled,
            signed_in=session is not None,
            configured=bool(ui.public_path),
            passkeys=passkey_state(request.headers),
            session_expires_at=session.expires_at if session else None,
        ),
    )


def check_setup_link(request: UiRequest, _match: re.Match[str]) -> None:
    require_same_origin(request)
    _require_configured(request.ui)  # before the form: completing it would be refused anyway
    body: SetupLinkCheck = _body(request, SetupLinkCheck)
    purpose = request.ui.service.owner.check_setup_link(body.token)
    _send_json(request, {"purpose": purpose})


def complete_setup(request: UiRequest, _match: re.Match[str]) -> None:
    require_same_origin(request)
    _require_configured(request.ui)
    body: SetupCompletion = _body(request, SetupCompletion)
    session = request.ui.service.owner.complete_setup(body.token, body.password)
    _send_json(request, {"signed_in": True}, cookie=_session_cookie(request.ui, session.token))


def login(request: UiRequest, _match: re.Match[str]) -> None:
    require_same_origin(request)
    _require_configured(request.ui)
    body: LoginRequest = _body(request, LoginRequest)
    session = request.ui.service.owner.login(body.password, body.code)
    _send_json(request, {"signed_in": True}, cookie=_session_cookie(request.ui, session.token))


def logout(request: UiRequest, _match: re.Match[str]) -> None:
    require_same_origin(request)
    try:
        request.ui.service.owner.logout(owner_session(request))
    except ServiceError:
        pass  # already gone; clear the cookie either way
    _send_json(request, {"signed_in": False}, cookie=_cleared_cookie(request.ui))


def confirm(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    body: LoginRequest = _body(request, LoginRequest)
    request.ui.service.owner.confirm(session, body.password, body.code)
    _send_json(request, {"confirmed": True})


def list_sessions(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    _send_json(request, request.ui.service.owner.list_sessions(session))


def revoke_other_sessions(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    _send_json(request, {"revoked": request.ui.service.owner.revoke_others(session)})


def change_password(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    body: PasswordChange = _body(request, PasswordChange)
    request.ui.service.owner.change_password(session, body.password)
    _send_json(request, {"changed": True})


def begin_totp(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    ui = request.ui
    secret, uri = ui.service.owner.begin_totp(session, account=ui.service.settings().profile_title)
    _send_json(request, TotpEnrollment(secret=secret, uri=uri, qr_svg=qr_data_uri(uri)))


def enable_totp(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    body: TotpCode = _body(request, TotpCode)
    request.ui.service.owner.enable_totp(session, body.code)
    _send_json(request, {"totp_enabled": True})


def disable_totp(request: UiRequest, _match: re.Match[str]) -> None:
    session = owner_session(request)
    request.ui.service.owner.disable_totp(session)
    _send_json(request, {"totp_enabled": False})


# -- views and the deployer's owner routes (behind `/api` scope checks) -------------------------


def fleet(request: UiRequest, _match: re.Match[str]) -> None:
    _send_json(request, fleet_view(request.ui.service))


def share(request: UiRequest, match: re.Match[str]) -> None:
    _send_json(request, share_view(request.ui.service, match[1]))


def owner_status(request: UiRequest, _match: re.Match[str]) -> OwnerStatus:
    return request.ui.service.owner.status()


def issue_setup_link(request: UiRequest, _match: re.Match[str]) -> SetupLinkGrant:
    """Only the deployer's root token may issue one: a session must never mint its own recovery."""
    if request.caller != ROOT:
        raise ServiceError("insufficient_scope", "Only the deployer's admin token issues setup links.", 403)
    body: SetupLinkRequest = _body(request, SetupLinkRequest)
    return request.ui.service.owner.issue_setup_link(reset=body.reset)


_USERNAME = f"({USERNAME_PATTERN})"
UiHandler = Callable[[Any, re.Match[str]], Any]
# (method, path, the `/api` scope it needs or None where the handler authenticates, handler)
UI_ROUTES: list[tuple[str, re.Pattern[str], TokenScope | None, UiHandler]] = [
    ("GET", re.compile(r"/"), None, page),
    ("GET", re.compile(r"/ui/((?:locales/)?[a-z0-9-]+\.(?:js|css|svg|json))"), None, asset),
    ("GET", re.compile(r"/auth/state"), None, auth_state),
    ("POST", re.compile(r"/auth/setup/check"), None, check_setup_link),
    ("POST", re.compile(r"/auth/setup"), None, complete_setup),
    ("POST", re.compile(r"/auth/login"), None, login),
    ("POST", re.compile(r"/auth/logout"), None, logout),
    ("POST", re.compile(r"/auth/confirm"), None, confirm),
    ("GET", re.compile(r"/auth/sessions"), None, list_sessions),
    ("POST", re.compile(r"/auth/sessions/revoke-others"), None, revoke_other_sessions),
    ("POST", re.compile(r"/auth/password"), None, change_password),
    ("POST", re.compile(r"/auth/totp/begin"), None, begin_totp),
    ("POST", re.compile(r"/auth/totp/enable"), None, enable_totp),
    ("POST", re.compile(r"/auth/totp/disable"), None, disable_totp),
    ("GET", re.compile(r"/ui/api/fleet"), "read", fleet),
    # Share links carry the subscription token, which connects as the person: admin, like `_visible`.
    ("GET", re.compile(rf"/ui/api/people/{_USERNAME}/share"), "admin", share),
]
