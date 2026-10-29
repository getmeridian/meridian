"""The web UI over HTTP: setup links, the session cookie, CSRF, scope mapping, step-up, and static files."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from meridian.control import passwords
from meridian.control.enrollment import token_sha256
from meridian.control.models import RevisionProposal, RevisionResult, ServiceSettings
from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from meridian.control.ui_routes import CONTENT_SECURITY_POLICY, SESSION_COOKIE, UI_DIR, passkey_state

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
PUBLIC_PATH = "/9f8e7d6c5b4a39281706f5e4"
PASSWORD = "correct horse battery"
UI = {"X-Meridian-Request": "1"}


class Clock:
    def __init__(self) -> None:
        self.now = float(int(time.time()))

    def __call__(self) -> float:
        return self.now


@dataclass
class Reply:
    status: int
    headers: Message
    body: Any = None
    raw: bytes = b""


@dataclass
class Fleet:
    url: str
    service: ControlService
    clock: Clock
    cookie: str = ""
    sent: list[str] = field(default_factory=list)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        bearer: str | None = None,
        session: bool = False,
    ) -> Reply:
        data = json.dumps(body).encode() if body is not None else (b"" if method in ("POST", "PUT") else None)
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        if bearer:
            request.add_header("Authorization", f"Bearer {bearer}")
        if session and self.cookie:
            request.add_header("Cookie", f"{SESSION_COOKIE}={self.cookie}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return _reply(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return _reply(exc.code, exc.headers, exc.read())

    def ui(self, method: str, path: str, body: Any = None, **kwargs: Any) -> Reply:
        """A request as the signed-in page sends it: cookie plus the UI's request header."""
        return self.call(method, path, body, headers={**UI, **kwargs.pop("headers", {})}, session=True, **kwargs)

    def set_up(self, password: str = PASSWORD) -> Reply:
        link = self.call("POST", "/api/owner/setup-link", {}, bearer=ADMIN_TOKEN).body["token"]
        reply = self.call("POST", "/auth/setup", {"token": link, "password": password}, headers=UI)
        self.cookie = _cookie_value(reply)
        return reply


def _reply(status: int, headers: Message, raw: bytes) -> Reply:
    try:
        body = json.loads(raw) if raw and headers.get("Content-Type", "").startswith("application/json") else None
    except ValueError:
        body = None
    return Reply(status, headers, body, raw)


def _cookie_value(reply: Reply) -> str:
    header = reply.headers.get("Set-Cookie", "")
    name, _, rest = header.partition("=")
    assert name == SESSION_COOKIE, header
    return rest.split(";", 1)[0]


def _serve(tmp_path: Path, public_path: str) -> Iterator[Fleet]:
    clock = Clock()
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), public_path=public_path)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield Fleet(f"http://127.0.0.1:{server.server_address[1]}", service, clock)
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "SCRYPT_PARAMS", (10, 8, 1))


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    yield from _serve(tmp_path, PUBLIC_PATH)


# -- setup links ------------------------------------------------------------------------------


def test_only_the_root_token_issues_setup_links(fleet: Fleet) -> None:
    admin = fleet.call("POST", "/api/tokens", {"name": "laptop", "scope": "admin"}, bearer=ADMIN_TOKEN).body["token"]
    assert fleet.call("POST", "/api/owner/setup-link", {}, bearer=admin).status == 403
    assert fleet.call("POST", "/api/owner/setup-link", {}).status == 401
    grant = fleet.call("POST", "/api/owner/setup-link", {}, bearer=ADMIN_TOKEN)
    assert grant.status == 200 and grant.body["purpose"] == "setup"
    fleet.set_up()
    assert fleet.ui("POST", "/api/owner/setup-link", {"reset": True}).status == 403  # a session never mints recovery


def test_owner_status_and_reset_links_follow_setup(fleet: Fleet) -> None:
    assert fleet.call("GET", "/api/owner", bearer=ADMIN_TOKEN).body == {"exists": False, "totp_enabled": False}
    fleet.set_up()
    assert fleet.call("GET", "/api/owner", bearer=ADMIN_TOKEN).body["exists"]
    refused = fleet.call("POST", "/api/owner/setup-link", {}, bearer=ADMIN_TOKEN)
    assert refused.status == 409 and refused.body["error"]["code"] == "owner_exists"
    reset = fleet.call("POST", "/api/owner/setup-link", {"reset": True}, bearer=ADMIN_TOKEN)
    assert reset.body["purpose"] == "reset"


def test_setup_sets_a_strict_session_cookie_on_the_secret_path(fleet: Fleet) -> None:
    reply = fleet.set_up()
    assert reply.status == 200
    attributes = [part.strip() for part in reply.headers["Set-Cookie"].split(";")]
    assert f"Path={PUBLIC_PATH}/" in attributes
    assert {"Secure", "HttpOnly", "SameSite=Strict"} <= set(attributes)
    assert reply.headers["Cache-Control"] == "no-store"
    assert fleet.cookie not in json.dumps(reply.body)


def test_a_spent_setup_link_is_gone(fleet: Fleet) -> None:
    link = fleet.call("POST", "/api/owner/setup-link", {}, bearer=ADMIN_TOKEN).body["token"]
    assert fleet.call("POST", "/auth/setup/check", {"token": link}, headers=UI).body == {"purpose": "setup"}
    assert fleet.call("POST", "/auth/setup", {"token": link, "password": PASSWORD}, headers=UI).status == 200
    again = fleet.call("POST", "/auth/setup", {"token": link, "password": PASSWORD}, headers=UI)
    assert again.status == 410 and again.body["error"]["code"] == "link_invalid"


def test_sign_in_stays_closed_without_the_secret_path(tmp_path: Path) -> None:
    for fleet in _serve(tmp_path, ""):
        link = fleet.call("POST", "/api/owner/setup-link", {}, bearer=ADMIN_TOKEN).body["token"]
        assert fleet.call("GET", "/auth/state").body["configured"] is False
        # Checked before the password form, too: the owner once typed a password only to be refused.
        checked = fleet.call("POST", "/auth/setup/check", {"token": link}, headers=UI)
        assert checked.status == 503 and checked.body["error"]["code"] == "ui_unconfigured"
        refused = fleet.call("POST", "/auth/setup", {"token": link, "password": PASSWORD}, headers=UI)
        assert refused.status == 503 and refused.body["error"]["code"] == "ui_unconfigured"


# -- CSRF ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Meridian-Request": "0"},
        {**UI, "Sec-Fetch-Site": "cross-site"},
        {**UI, "Sec-Fetch-Site": "same-site"},
        {**UI, "Origin": "https://attacker.example"},
    ],
    ids=["no-header", "wrong-header", "cross-site", "same-site", "foreign-origin"],
)
def test_sign_in_and_session_writes_refuse_cross_site_requests(fleet: Fleet, headers: dict[str, str]) -> None:
    fleet.set_up()
    login = fleet.call("POST", "/auth/login", {"password": PASSWORD}, headers=headers)
    assert login.status == 403 and login.body["error"]["code"] == "csrf"
    write = fleet.call("PUT", "/api/users/alice", headers=headers, session=True)
    assert write.status == 403 and write.body["error"]["code"] == "csrf"
    assert fleet.service.get_user("alice") is None


def test_a_same_origin_page_passes_the_csrf_checks(fleet: Fleet) -> None:
    fleet.set_up()
    host = fleet.url.removeprefix("http://")
    headers = {"Sec-Fetch-Site": "same-origin", "Origin": f"http://{host}"}
    assert fleet.ui("PUT", "/api/users/alice", headers=headers).status == 200


def test_session_reads_need_no_csrf_header(fleet: Fleet) -> None:
    fleet.set_up()
    assert fleet.call("GET", "/api/users", session=True).status == 200


# -- the session as a caller -----------------------------------------------------------------------


def test_a_session_holds_the_admin_scope_and_sees_credentials(fleet: Fleet) -> None:
    fleet.set_up()
    assert fleet.ui("PUT", "/api/users/alice").status == 200
    user = fleet.ui("GET", "/api/users/alice").body
    assert user["uuid"] and user["sub_token"]
    assert fleet.ui("GET", "/api/tokens").status == 200


def test_revisions_a_session_records_name_the_owner(fleet: Fleet) -> None:
    fleet.set_up()
    proposal = {"intent": {"access": {"users": ["alice"]}}, "plan_hash": "1" * 64, "review_hash": "2" * 64}
    created = fleet.ui("POST", "/api/revisions", proposal)
    assert created.status == 200 and created.body["author"] == {"token_id": None, "name": "owner"}


def test_bearer_wins_over_a_session_cookie(fleet: Fleet) -> None:
    fleet.set_up()
    read = fleet.call("POST", "/api/tokens", {"name": "viewer", "scope": "read"}, bearer=ADMIN_TOKEN).body["token"]
    reply = fleet.call("PUT", "/api/users/alice", headers=UI, bearer=read, session=True)
    assert reply.status == 403 and reply.body["error"]["code"] == "insufficient_scope"


def test_without_a_session_the_api_answers_401(fleet: Fleet) -> None:
    fleet.set_up()
    fleet.cookie = "not-a-session"
    assert fleet.ui("GET", "/api/users").status == 401
    assert fleet.call("GET", "/auth/state", session=True).body["signed_in"] is False


def test_minting_a_token_needs_a_recent_confirmation(fleet: Fleet) -> None:
    fleet.set_up()
    fleet.clock.now += CONFIRM_SECONDS + 1
    refused = fleet.ui("POST", "/api/tokens", {"name": "ci", "scope": "read"})
    assert refused.status == 403 and refused.body["error"]["code"] == "reauth_required"
    assert fleet.ui("POST", "/auth/confirm", {"password": "wrong password!"}).status == 401
    assert fleet.ui("POST", "/auth/confirm", {"password": PASSWORD}).status == 200
    issued = fleet.ui("POST", "/api/tokens", {"name": "ci", "scope": "read"})
    assert issued.status == 200 and issued.body["token"].startswith("mrd_")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/nodes/main/enrollment", {}),
        ("POST", "/api/import/users", {"users": []}),
        ("GET", "/api/backup/devices", None),
        ("POST", "/api/backup/devices", {"name": "thief", "public_key": "age1x"}),
        ("DELETE", "/api/backup/devices/0123456789ab", None),
        ("GET", "/api/backup/recovery-key", None),
        ("PUT", "/api/backup/recovery-key", {"public_key": "age1x"}),
        ("PUT", "/api/backup/deployer-state", {}),
        ("GET", "/api/backup/snapshot", None),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("/") else None,
)
def test_a_session_never_reaches_cli_only_routes_even_just_confirmed(
    fleet: Fleet, method: str, path: str, body: Any
) -> None:
    """A borrowed browser could seal a snapshot to its own key or enroll a rogue node; the console calls neither."""
    fleet.set_up()  # signed in this second: step-up would pass
    refused = fleet.ui(method, path, body)
    assert refused.status == 403 and refused.body["error"]["code"] == "insufficient_scope"
    assert fleet.call(method, path, body, bearer=ADMIN_TOKEN).status != 403


def test_logout_clears_the_cookie_and_ends_the_session(fleet: Fleet) -> None:
    fleet.set_up()
    reply = fleet.ui("POST", "/auth/logout", {})
    assert "Max-Age=0" in reply.headers["Set-Cookie"]
    assert fleet.ui("GET", "/api/users").status == 401


def test_sign_out_others_over_http(fleet: Fleet) -> None:
    fleet.set_up()
    mine = fleet.cookie
    login = fleet.call("POST", "/auth/login", {"password": PASSWORD}, headers=UI)
    other = _cookie_value(login)
    assert len(fleet.ui("GET", "/auth/sessions").body["sessions"]) == 2
    assert fleet.ui("POST", "/auth/sessions/revoke-others", {}).body == {"revoked": 1}
    fleet.cookie = other
    assert fleet.ui("GET", "/api/users").status == 401
    fleet.cookie = mine
    assert fleet.ui("GET", "/api/users").status == 200


def test_repeated_wrong_passwords_answer_429_with_retry_after(fleet: Fleet) -> None:
    fleet.set_up()
    for _ in range(5):
        assert fleet.call("POST", "/auth/login", {"password": "wrong password!"}, headers=UI).status == 401
    locked = fleet.call("POST", "/auth/login", {"password": PASSWORD}, headers=UI)
    assert locked.status == 429 and int(locked.headers["Retry-After"]) >= 1


# -- views ---------------------------------------------------------------------------------------


def _declare(service: ControlService, *names: str) -> int:
    """Record an applied revision whose intent declares `names`, as the deployer's apply does."""
    proposal = RevisionProposal(intent={"access": {"users": list(names)}}, plan_hash="1" * 64, review_hash="2" * 64)
    number = service.revisions.propose(proposal, caller=ROOT).number
    service.revisions.approve(number, caller=ROOT)
    service.revisions.record_result(number, RevisionResult(status="applied"))
    return number


def test_share_links_need_admin_and_pages_only_for_declared_people(fleet: Fleet) -> None:
    service = fleet.service
    service.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://198.51.100.7/c0nn"))
    for name in ("alice", "bob"):
        service.put_user(name)
    number = _declare(service, "alice")
    read = fleet.call("POST", "/api/tokens", {"name": "viewer", "scope": "read"}, bearer=ADMIN_TOKEN).body["token"]
    assert fleet.call("GET", "/ui/api/people/alice/share", bearer=read).status == 403
    alice = fleet.call("GET", "/ui/api/people/alice/share", bearer=ADMIN_TOKEN).body
    user = service.get_user("alice")
    assert user is not None
    assert alice["subscription_url"] == f"https://198.51.100.7/c0nn/sub/{user.sub_token}"
    assert alice["page_url"] == f"https://198.51.100.7/c0nn/{user.uuid}/"
    assert alice["qr_svg"].startswith("data:image/svg+xml")
    assert alice["declared"] is True
    bob = fleet.call("GET", "/ui/api/people/bob/share", bearer=ADMIN_TOKEN).body
    assert bob["page_url"] == "" and bob["declared"] is False
    view = fleet.call("GET", "/ui/api/fleet", bearer=read).body
    assert view["declared_users"] == ["alice"] and view["intent_revision"] == number


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("198.51.100.7", "no_domain"),
        ("[2001:db8::1]", "no_domain"),
        ("localhost:8443", "no_domain"),
        ("console.example.com", "not_built"),
    ],
    ids=str,
)
def test_passkeys_are_never_offered_on_an_ip_address(host: str, reason: str) -> None:
    headers = Message()
    headers["Host"] = host
    state = passkey_state(headers)
    assert not state.available and state.reason == reason


# -- static files ----------------------------------------------------------------------------------


def test_page_carries_strict_security_headers_and_is_never_cached(fleet: Fleet) -> None:
    reply = fleet.call("GET", "/")
    assert reply.status == 200 and reply.headers["Content-Type"].startswith("text/html")
    assert reply.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY and "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert reply.headers["Referrer-Policy"] == "no-referrer"
    assert reply.headers["X-Frame-Options"] == "DENY"
    assert reply.headers["Cache-Control"] == "no-store"


def test_assets_revalidate_by_etag_and_honour_weak_tags(fleet: Fleet) -> None:
    first = fleet.call("GET", "/ui/core.js")
    etag = first.headers["ETag"]
    assert first.status == 200 and first.headers["Cache-Control"] == "no-cache"
    assert first.headers["Content-Type"] == "text/javascript"
    assert fleet.call("GET", "/ui/core.js", headers={"If-None-Match": etag}).status == 304
    assert fleet.call("GET", "/ui/core.js", headers={"If-None-Match": f"W/{etag}"}).status == 304
    assert fleet.call("GET", "/ui/locales/fa.json").status == 200


@pytest.mark.parametrize(
    "path", ["/ui/CLAUDE.md", "/ui/index.html", "/ui/../server.py", "/ui/%2e%2e/server.py", "/ui/missing.js"], ids=str
)
def test_only_shipped_ui_files_are_served(fleet: Fleet, path: str) -> None:
    assert fleet.call("GET", path).status == 404


def test_every_shipped_asset_is_reachable(fleet: Fleet) -> None:
    for path in sorted(UI_DIR.rglob("*")):
        relative = path.relative_to(UI_DIR).as_posix()
        if path.is_file() and path.suffix in (".js", ".css", ".svg", ".json"):
            assert fleet.call("GET", f"/ui/{relative}").status == 200, relative


def test_declared_people_are_the_clis_to_remove_or_relink(fleet: Fleet) -> None:
    """The CLI moves a declared person's page under the new UUID; a console reissue left it at the old one."""
    fleet.set_up()
    service = fleet.service
    for name in ("alice", "bob"):
        service.put_user(name)
    _declare(service, "alice")
    before = service.get_user("alice")
    assert before is not None
    for method, path in (("POST", "/api/users/alice/reissue"), ("DELETE", "/api/users/alice")):
        refused = fleet.ui(method, path, {} if method == "POST" else None)
        assert refused.status == 409 and refused.body["error"]["code"] == "declared_user", path
    after = service.get_user("alice")
    assert after is not None and after.uuid == before.uuid
    assert fleet.ui("GET", "/ui/api/people/alice/share").body["declared"] is True
    assert fleet.ui("POST", "/api/users/bob/reissue", {}).status == 200  # the console's own people stay its own
    assert fleet.ui("DELETE", "/api/users/bob").status == 200
    assert fleet.call("POST", "/api/users/alice/reissue", {}, bearer=ADMIN_TOKEN).status == 200  # the CLI may
    assert fleet.call("DELETE", "/api/users/alice", bearer=ADMIN_TOKEN).status == 200
