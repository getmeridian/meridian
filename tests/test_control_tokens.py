"""Scoped API tokens: who may call which `/api` route, revocation, last use, and the schema step that adds them."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceScopeError
from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.models import (
    TOKEN_SCOPES,
    ClientEndpoint,
    PrivacySettings,
    RevisionProposal,
    RevisionResult,
    TokenCreate,
    TokenRecord,
    UserCreate,
    UserUpdate,
    scope_covers,
)
from meridian.control.server import ROUTES, ControlServer
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import generate_key_pair
from meridian.control.tokens import LAST_USED_RESOLUTION_SECONDS, ROOT, Caller
from tests.support.control_service import drop_device_privacy_schema, drop_operations_schema

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
ENDPOINT = ClientEndpoint(
    remark="Reality",
    protocol="reality",
    address="198.51.100.7",
    port=443,
    position=0,
    fingerprint="chrome",
    reality_sni="www.example.com",
    reality_public_key="PBK",
    reality_short_id="abcd",
).model_dump(mode="json")


class Clock:
    def __init__(self) -> None:
        self.now = float(int(time.time()))

    def __call__(self) -> float:
        return self.now


@dataclass
class Fleet:
    url: str
    service: ControlService
    clock: Clock


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    clock = Clock()
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    # A short poll keeps shutdown quick across the scope matrix's many servers.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield Fleet(f"http://127.0.0.1:{server.server_address[1]}", service, clock)
    server.shutdown()
    server.server_close()


def _request(
    url: str, method: str = "GET", body: Any = None, token: str = "", headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def _mint(fleet: Fleet, scope: str, name: str = "device") -> dict[str, Any]:
    status, issued = _request(f"{fleet.url}/api/tokens", "POST", {"name": name, "scope": scope}, ADMIN_TOKEN)
    assert status == 200
    return issued


BACKUP_KEY = generate_key_pair().public
# Samples carry no permissions: every requirement comes directly from server.ROUTES.
ROUTE_SAMPLES = {
    "openapi": ("/api/openapi.json", None),
    "health": ("/api/health", None),
    "get_settings": ("/api/settings", None),
    "put_settings": ("/api/settings", {"profile_title": "Fleet"}),
    "get_privacy": ("/api/privacy", None),
    "put_privacy": ("/api/privacy", {}),
    "get_notice": ("/api/notice", None),
    "put_notice": ("/api/notice", {"notice": {"text": "Sample notice"}, "confirm": True}),
    "get_language": ("/api/language", None),
    "put_language": ("/api/language", {"language": "ru"}),
    "subscription_preview": ("/api/subscription/preview", {"username": "alice", "user_agent": "Hiddify/2"}),
    "get_node": ("/api/nodes/main", None),
    "put_node": ("/api/nodes/edge", {"resources": []}),
    "delete_node": ("/api/nodes/main", None),
    "grant_enrollment": ("/api/nodes/main/enrollment", {}),
    "issue_command": ("/api/nodes/main/commands", {"kind": "reconcile"}),
    "get_command": ("/api/nodes/main/commands/00000000-0000-4000-8000-000000000000", None),
    "command_targets": ("/api/nodes/main/command-targets", None),
    "fleet_authority": ("/api/ca", None),
    "traffic": ("/api/traffic", None),
    "events": ("/api/events", None),
    "attention": ("/api/attention", None),
    "server_usage": ("/api/servers", None),
    "daily_traffic": ("/api/traffic/daily", None),
    "list_users": ("/api/users", None),
    "create_user": ("/api/users", {"username": "bob", "traffic_limit_bytes": 1000}),
    "import_users": ("/api/import/users", {"users": []}),
    "get_user": ("/api/users/alice", None),
    "user_connections": ("/api/users/alice/connections", None),
    "user_usage": ("/api/users/alice/usage", None),
    "put_user": ("/api/users/bob", {}),
    "update_user": ("/api/users/alice", {"status": "disabled"}),
    "patch_user": ("/api/users", {"username": "alice", "status": "disabled"}),
    "reissue_user": ("/api/users/alice/reissue", {}),
    "cut_off_scope": ("/api/users/alice/cut-off", None),
    "reset_user_traffic": ("/api/users/alice/reset-traffic", {}),
    "extend_user": ("/api/users/alice/extend", {"days": 7}),
    "user_devices": ("/api/users/alice/devices", None),
    "delete_user_device": ("/api/users/alice/devices/" + "a" * 64, None),
    "clear_user_devices": ("/api/users/alice/devices", None),
    "delete_user": ("/api/users/alice", None),
    "get_endpoint": ("/api/endpoints/main:reality", None),
    "put_endpoint": ("/api/endpoints/main:xhttp", ENDPOINT),
    "delete_endpoint": ("/api/endpoints/main:reality", None),
    "list_tokens": ("/api/tokens", None),
    "create_token": ("/api/tokens", {"name": "laptop", "scope": "read"}),
    "revoke_token": ("/api/tokens/{victim}", None),
    "list_revisions": ("/api/revisions", None),
    "propose_revision": ("/api/revisions", {"intent": {}, "plan_hash": "a" * 64, "review_hash": "b" * 64}),
    "get_revision": ("/api/revisions/1", None),
    "approve_revision": ("/api/revisions/1/approve", {}),
    "record_revision_result": ("/api/revisions/1/result", {"status": "applied"}),
    "get_fence": ("/api/fence", None),
    "swap_fence": ("/api/fence", {}),
    "list_device_keys": ("/api/backup/devices", None),
    "register_device_key": ("/api/backup/devices", {"name": "laptop", "public_key": BACKUP_KEY}),
    "revoke_device_key": ("/api/backup/devices/{victim}", None),
    "get_recovery_key": ("/api/backup/recovery-key", None),
    "put_recovery_key": ("/api/backup/recovery-key", {"public_key": BACKUP_KEY}),
    "deposit_deployer_state": (
        "/api/backup/deployer-state",
        {"cluster_yml": "version: 3\n", "servers_json": "[]", "generation": 0},
    ),
    "snapshot": ("/api/backup/snapshot", None),
    "owner_status": ("/api/owner", None),
    "issue_setup_link": ("/api/owner/setup-link", {}),
}
SCOPE_MATRIX = [
    (method, *ROUTE_SAMPLES[handler.__name__], needed)
    for method, pattern, needed, handler in ROUTES
    if pattern.pattern.startswith("/api")
]


@pytest.mark.parametrize("held", [*TOKEN_SCOPES, "root"])
@pytest.mark.parametrize(
    ("method", "path", "body", "needed"), SCOPE_MATRIX, ids=[f"{row[0]} {row[1]}" for row in SCOPE_MATRIX]
)
def test_scope_matrix(fleet: Fleet, method: str, path: str, body: Any, needed: Any, held: str) -> None:
    assert _request(f"{fleet.url}/api/users/alice", "PUT", {}, ADMIN_TOKEN)[0] == 200
    assert _request(f"{fleet.url}/api/nodes/main", "PUT", {"resources": []}, ADMIN_TOKEN)[0] == 200
    assert _request(f"{fleet.url}/api/endpoints/main:reality", "PUT", ENDPOINT, ADMIN_TOKEN)[0] == 200
    victim = _mint(fleet, "read", "victim")["id"]
    token = ADMIN_TOKEN if held == "root" else _mint(fleet, held)["token"]

    status, answer = _request(fleet.url + path.format(victim=victim), method, body, token)

    requirements = (needed,) if isinstance(needed, str) else needed
    if held == "root" or any(scope_covers(held, scope) for scope in requirements):
        if path == "/api/owner/setup-link" and held != "root":
            assert status == 403 and answer["error"]["code"] == "insufficient_scope"
        else:
            assert status not in (401, 403), answer
    else:
        assert status == 403 and answer["error"]["code"] == "insufficient_scope"


def test_every_admin_route_declares_a_scope() -> None:
    undeclared = [
        pattern.pattern for _, pattern, scope, _ in ROUTES if pattern.pattern.startswith("/api") and not scope
    ]
    assert undeclared == []


def test_every_api_route_has_a_matching_permission_sample() -> None:
    for _method, pattern, _scope, handler in ROUTES:
        if pattern.pattern.startswith("/api"):
            assert pattern.fullmatch(ROUTE_SAMPLES[handler.__name__][0].format(victim="0123456789ab")), handler.__name__


def test_people_access_is_declared_only_on_user_routes() -> None:
    for _method, pattern, needed, handler in ROUTES:
        if needed is None:
            continue
        requirements = (needed,) if isinstance(needed, str) else needed
        manages_people = (
            pattern.pattern.startswith("/api/users") and handler.__name__ != "cut_off_scope"
        ) or handler.__name__ == "openapi"
        assert any(scope_covers("people", scope) for scope in requirements) == manages_people
        assert all(scope_covers("admin", scope) for scope in requirements)


@pytest.mark.parametrize(
    "credential",
    ["", "not-a-token", "mrd_000000000000_" + "0" * 64, "wrong-secret", "revoked"],
    ids=["missing", "garbage", "unknown-id", "wrong-secret", "revoked"],
)
def test_a_missing_wrong_or_revoked_token_is_unauthenticated(fleet: Fleet, credential: str) -> None:
    issued = _mint(fleet, "admin")
    if credential == "wrong-secret":
        credential = issued["token"][:-1] + ("0" if issued["token"][-1] != "0" else "1")
    elif credential == "revoked":
        assert _request(f"{fleet.url}/api/tokens/{issued['id']}", "DELETE", token=ADMIN_TOKEN)[0] == 200
        credential = issued["token"]

    status, body = _request(f"{fleet.url}/api/health", token=credential)

    assert (status, body) == (401, {"error": {"code": "unauthenticated", "message": "Authentication required."}})


def test_revoking_takes_effect_on_the_next_request(fleet: Fleet) -> None:
    issued = _mint(fleet, "read")
    with ControlClient(fleet.url, issued["token"]) as device, ControlClient(fleet.url, ADMIN_TOKEN) as root:
        assert device.health().fleet_id == FLEET_ID
        revoked = root.revoke_token(issued["id"])
        assert revoked.revoked_at is not None
        with pytest.raises(ControlServiceAuthError):
            device.health()
        assert root.revoke_token(issued["id"]).revoked_at == revoked.revoked_at


def test_a_token_may_revoke_itself(fleet: Fleet) -> None:
    issued = _mint(fleet, "admin")
    with ControlClient(fleet.url, issued["token"]) as device:
        assert device.revoke_token(issued["id"]).revoked_at is not None
        with pytest.raises(ControlServiceAuthError):
            device.list_tokens()


def test_a_token_can_never_mint_a_scope_above_its_own(fleet: Fleet) -> None:
    issued = fleet.service.tokens.issue(TokenCreate(name="approver", scope="approve"), issuer=ROOT)
    approver = fleet.service.tokens.authenticate(issued.token)
    with pytest.raises(ServiceError) as refused:
        fleet.service.tokens.issue(TokenCreate(name="escalate", scope="admin"), issuer=approver)
    assert (refused.value.status, refused.value.code) == (403, "insufficient_scope")
    assert fleet.service.tokens.issue(TokenCreate(name="reader", scope="read"), issuer=approver).scope == "read"


def test_too_little_scope_is_a_scope_error_on_the_client(fleet: Fleet) -> None:
    with ControlClient(fleet.url, _mint(fleet, "read")["token"]) as device, pytest.raises(ControlServiceScopeError):
        device.put_user("mallory")


def test_listing_carries_metadata_never_secrets_or_hashes(fleet: Fleet) -> None:
    issued = _mint(fleet, "propose", "phone")
    status, body = _request(f"{fleet.url}/api/tokens", token=ADMIN_TOKEN)

    assert status == 200 and set(body["tokens"][0]) == set(TokenRecord.model_fields)
    assert body["tokens"][0]["name"] == "phone" and body["tokens"][0]["scope"] == "propose"
    text = json.dumps(body)
    assert issued["token"] not in text and token_sha256(issued["token"]) not in text
    assert token_sha256(ADMIN_TOKEN) not in text


def test_the_configured_admin_token_keeps_full_access_and_stays_unlisted(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        issued = root.create_token("laptop", "admin")
        assert [item.id for item in root.list_tokens()] == [issued.id]
        root.revoke_token(issued.id)
        assert root.health().fleet_id == FLEET_ID


def test_last_use_is_recorded_at_most_once_a_minute(fleet: Fleet) -> None:
    issued = _mint(fleet, "read")
    tokens = fleet.service.tokens
    assert tokens.get(issued["id"]).last_used_at is None

    tokens.authenticate(issued["token"])
    first = tokens.get(issued["id"]).last_used_at
    fleet.clock.now += LAST_USED_RESOLUTION_SECONDS - 1
    tokens.authenticate(issued["token"])
    assert first is not None and tokens.get(issued["id"]).last_used_at == first

    fleet.clock.now += 1
    tokens.authenticate(issued["token"])
    assert tokens.get(issued["id"]).last_used_at != first


def test_no_client_address_reaches_the_database(fleet: Fleet, tmp_path: Path) -> None:
    address = "198.51.100.23"
    issued = _mint(fleet, "read")
    status, _ = _request(f"{fleet.url}/api/health", token=issued["token"], headers={"X-Forwarded-For": address})
    assert status == 200
    stored = b"".join(path.read_bytes() for path in tmp_path.glob("control.db*"))
    assert address.encode() not in stored and b"127.0.0.1" not in stored


def test_a_version_three_database_gains_api_tokens(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    db = sqlite3.connect(path)
    db.execute("DROP TABLE api_tokens")
    db.execute("ALTER TABLE users DROP COLUMN traffic_carried_bytes")  # added in version 6
    drop_device_privacy_schema(db)
    db.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
    db.commit()
    db.close()

    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")

    db = sqlite3.connect(path)
    assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(SCHEMA_VERSION),)
    db.close()
    assert [user.username for user in service.list_users()] == ["alice"]
    issued = service.tokens.issue(TokenCreate(name="laptop", scope="read"), issuer=Caller(None, "admin"))
    assert service.tokens.authenticate(issued.token).scope == "read"


@pytest.mark.parametrize(
    ("scope", "sees"), [("read", False), ("propose", False), ("approve", False), ("people", True), ("admin", True)]
)
def test_only_people_and_admin_read_the_credentials_that_connect_as_a_user(
    fleet: Fleet, scope: str, sees: bool
) -> None:
    alice = fleet.service.put_user("alice")
    token = _mint(fleet, scope)["token"]

    _status, listed = _request(f"{fleet.url}/api/users", token=token)
    _status, one = _request(f"{fleet.url}/api/users/alice", token=token)

    for record in (listed["users"][0], one):
        assert record["username"] == "alice"
        assert (record["uuid"], record["sub_token"]) == ((alice.uuid, alice.sub_token) if sees else ("", ""))


def test_people_is_orthogonal_to_revision_scopes_and_admin_covers_it() -> None:
    assert scope_covers("people", "people")
    assert scope_covers("admin", "people")
    for scope in ("read", "propose", "approve"):
        assert not scope_covers(scope, "people")
        assert not scope_covers("people", scope)


def test_people_reads_credentials_and_edits_people_without_fleet_access(fleet: Fleet) -> None:
    alice = fleet.service.put_user("alice")
    token = _mint(fleet, "people")["token"]
    for path in ("/api/users", "/api/users/alice"):
        status, answer = _request(fleet.url + path, token=token)
        assert status == 200
        record = answer["users"][0] if "users" in answer else answer
        assert (record["uuid"], record["sub_token"]) == (alice.uuid, alice.sub_token)
    status, updated = _request(
        f"{fleet.url}/api/users/alice", "PATCH", {"status": "disabled", "traffic_limit_bytes": 1000}, token
    )
    assert status == 200 and (updated["status"], updated["traffic_limit_bytes"]) == ("disabled", 1000)
    for method, path, body, _needed in SCOPE_MATRIX:
        if path.startswith("/api/users") or path == "/api/openapi.json":
            continue
        status, answer = _request(fleet.url + path.format(victim="0123456789ab"), method, body, token)
        assert (status, answer["error"]["code"]) == (403, "insufficient_scope"), path
    for path in ("/ui/api/fleet", "/ui/api/people/alice/share"):
        assert _request(fleet.url + path, token=token)[0] == 403


def test_people_can_manage_console_people_and_devices_with_credentials(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        root.set_privacy(PrivacySettings(device_limits="private"))
    with ControlClient(fleet.url, _mint(fleet, "people")["token"]) as client:
        created = client.create_user(UserCreate(username="bob", traffic_limit_bytes=1000, device_limit=2))
        assert client.get_user("bob") == created
        assert client.list_users() == [created]
        fleet.service.subscription(created.sub_token, device_headers={"x-hwid": "test-device-0001"})
        devices = client.user_devices("bob")
        assert devices.count == 1
        assert client.delete_user_devices("bob", devices.devices[0].device_key).deleted == 1
        changed = client.update_user("bob", UserUpdate(status="disabled", device_limit=3))
        assert (changed.status, changed.device_limit) == ("disabled", 3)
        assert client.reset_user_traffic("bob").used_traffic_bytes == 0
        assert client.extend_user("bob", days=7).status == "disabled"
        reissued = client.reissue_user("bob")
        assert reissued.uuid != created.uuid and reissued.sub_token != created.sub_token
        assert client.user_devices("bob").devices == []
        assert client.delete_user_devices("bob", "a" * 64).deleted == 0
        assert client.delete_user_devices("bob").deleted == 0
        client.delete_user("bob")
        assert client.get_user("bob") is None


def _declare(fleet: Fleet, status: str) -> None:
    revision = fleet.service.revisions.propose(
        RevisionProposal(intent={"access": {"users": ["alice"]}}, plan_hash="a" * 64, review_hash="b" * 64), caller=ROOT
    )
    if status != "proposed":
        fleet.service.revisions.approve(revision.number, caller=ROOT)
    if status in ("applied", "failed"):
        fleet.service.revisions.record_result(revision.number, RevisionResult(status=status))


@pytest.mark.parametrize("status", ["proposed", "approved", "failed", "applied"])
@pytest.mark.parametrize(("method", "path"), [("POST", "/api/users/alice/reissue"), ("DELETE", "/api/users/alice")])
def test_people_cannot_remove_or_reissue_declared_people(fleet: Fleet, status: str, method: str, path: str) -> None:
    before = fleet.service.put_user("alice")
    _declare(fleet, status)
    token = _mint(fleet, "people")["token"]
    code, answer = _request(fleet.url + path, method, {} if method == "POST" else None, token)
    if status == "proposed":
        assert code == 200
    else:
        assert (code, answer["error"]["code"]) == (409, "declared_user")
        assert fleet.service.get_user("alice") == before


def test_people_declared_refusal_is_checked_inside_the_write(fleet: Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    before = fleet.service.put_user("alice")
    original = fleet.service.reissue_user

    def declare_then_reissue(username: str) -> Any:
        _declare(fleet, "approved")
        return original(username)

    monkeypatch.setattr(fleet.service, "reissue_user", declare_then_reissue)
    code, answer = _request(fleet.url + "/api/users/alice/reissue", "POST", {}, _mint(fleet, "people")["token"])
    assert (code, answer["error"]["code"]) == (409, "declared_user")
    assert fleet.service.get_user("alice") == before


@pytest.mark.parametrize("days", [0, 3651, -1, 1.5, "7", None, True])
def test_extend_api_requires_one_bounded_integer(fleet: Fleet, days: Any) -> None:
    original = fleet.service.put_user("alice")
    status, answer = _request(fleet.url + "/api/users/alice/extend", "POST", {"days": days}, ADMIN_TOKEN)
    assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert fleet.service.get_user("alice") == original


def test_token_expiry_refuses_at_the_boundary_like_revocation(fleet: Fleet) -> None:
    status, issued = _request(
        f"{fleet.url}/api/tokens", "POST", {"name": "temporary", "scope": "read", "expires_in_days": 1}, ADMIN_TOKEN
    )
    assert status == 200, issued
    assert issued["expires_at"] is not None
    fleet.clock.now += 86400 - 1
    assert _request(f"{fleet.url}/api/health", token=issued["token"])[0] == 200
    last_used = fleet.service.tokens.get(issued["id"]).last_used_at
    fleet.clock.now += 1
    assert _request(f"{fleet.url}/api/health", token=issued["token"]) == (
        401,
        {"error": {"code": "unauthenticated", "message": "Authentication required."}},
    )
    assert fleet.service.tokens.get(issued["id"]).last_used_at == last_used


@pytest.mark.parametrize("days", [1, 3650, None])
def test_client_token_expiry_uses_the_registry_clock_and_supports_never(fleet: Fleet, days: int | None) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as client:
        issued = client.create_token("temporary", "read", expires_in_days=days)
    expected = datetime.fromtimestamp(fleet.clock.now, UTC) + timedelta(days=days or 0)
    assert issued.expires_at == (expected.strftime("%Y-%m-%dT%H:%M:%SZ") if days is not None else None)
    assert fleet.service.tokens.get(issued.id).expires_at == issued.expires_at
    if days is None:
        fleet.clock.now += 3651 * 86400
        assert fleet.service.tokens.authenticate(issued.token).scope == "read"


@pytest.mark.parametrize("days", [0, -1, 3651, 1.5, "7", True])
def test_token_api_refuses_invalid_expiry_durations(fleet: Fleet, days: Any) -> None:
    before = fleet.service.tokens.list_tokens()
    status, answer = _request(
        fleet.url + "/api/tokens", "POST", {"name": "temporary", "scope": "read", "expires_in_days": days}, ADMIN_TOKEN
    )
    assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert fleet.service.tokens.list_tokens() == before


def test_absolute_token_expiry_is_precise_and_refused_before_scope_checks(fleet: Fleet) -> None:
    initial = fleet.clock.now
    expiry = datetime.fromtimestamp(fleet.clock.now, UTC).strftime("%Y-%m-%dT%H:%M:%S.5Z")
    status, issued = _request(
        fleet.url + "/api/tokens", "POST", {"name": "temporary", "scope": "people", "expires_at": expiry}, ADMIN_TOKEN
    )
    assert status == 200, issued
    fleet.clock.now = initial + 0.49
    assert _request(fleet.url + "/api/users", token=issued["token"])[0] == 200
    fleet.clock.now = initial + 0.5
    assert _request(fleet.url + "/api/nodes/main", token=issued["token"])[0] == 401


def test_token_create_requires_one_future_expiry_choice(fleet: Fleet) -> None:
    for extra in (
        {"expires_at": "2999-01-01T00:00:00Z", "expires_in_days": 7},
        {"expires_at": "2020-01-01T00:00:00Z"},
    ):
        status, answer = _request(
            fleet.url + "/api/tokens", "POST", {"name": "temporary", "scope": "read", **extra}, ADMIN_TOKEN
        )
        assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert fleet.service.tokens.list_tokens().tokens == []


def test_token_api_refuses_an_impossible_calendar_expiry(fleet: Fleet) -> None:
    before = fleet.service.tokens.list_tokens()
    status, answer = _request(
        fleet.url + "/api/tokens",
        "POST",
        {"name": "temporary", "scope": "read", "expires_at": "2026-02-31T00:00:00Z"},
        ADMIN_TOKEN,
    )
    assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert fleet.service.tokens.list_tokens() == before


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_user_api_refuses_impossible_expiry_even_while_disabled(fleet: Fleet, method: str) -> None:
    fleet.service.put_user("alice")
    before = fleet.service.list_users()
    body = {"status": "disabled", "expire_at": "2026-02-31T00:00:00Z"}
    path = "/api/users/alice"
    if method == "POST":
        body["username"] = "bob"
        path = "/api/users"
    status, answer = _request(fleet.url + path, method, body, ADMIN_TOKEN)
    assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert fleet.service.list_users() == before


@pytest.mark.parametrize("expiry", ["2099-01-01T00:00:00Z", "9999-12-31T23:59:59Z"])
@pytest.mark.parametrize("write", ["create", "update", "import-create", "import-replace"])
def test_user_expiry_writes_refuse_the_legacy_never_range(fleet: Fleet, expiry: str, write: str) -> None:
    existing = fleet.service.put_user("sample")
    before = fleet.service.list_users()
    body: dict[str, Any] = {"status": "disabled", "expire_at": expiry}
    method, path = "POST", "/api/users"
    if write == "create":
        body["username"] = "new"
    elif write == "update":
        method, path = "PATCH", "/api/users/sample"
    else:
        replace = write == "import-replace"
        body.update(
            username="sample" if replace else "new",
            uuid=existing.uuid if replace else "00000000-0000-4000-8000-000000000123",
            sub_token=existing.sub_token if replace else "test-subscription-new",
        )
        body = {"users": [body], "replace": replace}
        path = "/api/import/users"
    status, answer = _request(fleet.url + path, method, body, ADMIN_TOKEN)
    assert status == 400, answer
    assert answer["error"]["code"] == "invalid_request"
    assert "before 2099" in answer["error"]["message"] and "Remove the expiry" in answer["error"]["message"]
    assert fleet.service.list_users() == before


def test_step_twelve_preserves_existing_tokens_without_expiry_and_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    issued = service.tokens.issue(TokenCreate(name="existing", scope="read"), issuer=ROOT)
    service.close()
    with sqlite3.connect(path) as db:
        drop_operations_schema(db)
        if "expires_at" in {column[1] for column in db.execute("PRAGMA table_info(api_tokens)")}:
            db.execute("ALTER TABLE api_tokens DROP COLUMN expires_at")
        db.execute("UPDATE meta SET value = '11' WHERE key = 'schema_version'")
    for _ in range(2):
        service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
        try:
            assert service.tokens.authenticate(issued.token).scope == "read"
            assert service.tokens.get(issued.id).expires_at is None
            assert service._store.meta("schema_version") == str(SCHEMA_VERSION)
        finally:
            service.close()
