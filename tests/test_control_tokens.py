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
from pathlib import Path
from typing import Any

import pytest

from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceScopeError
from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.models import TOKEN_SCOPES, ClientEndpoint, TokenCreate, TokenRecord
from meridian.control.server import ROUTES, ControlServer
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import generate_key_pair
from meridian.control.tokens import LAST_USED_RESOLUTION_SECONDS, Caller
from tests.support.control_service import drop_device_privacy_schema

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
# The scope each `/api` route needs; `{victim}` is another token's ID.
SCOPE_MATRIX = [
    ("GET", "/api/health", None, "read"),
    ("GET", "/api/settings", None, "read"),
    ("GET", "/api/nodes/main", None, "read"),
    ("GET", "/api/ca", None, "read"),
    ("GET", "/api/traffic", None, "read"),
    ("GET", "/api/users", None, "read"),
    ("GET", "/api/users/alice", None, "read"),
    ("GET", "/api/endpoints/main:reality", None, "read"),
    ("GET", "/api/revisions", None, "read"),
    ("GET", "/api/fence", None, "read"),
    ("POST", "/api/revisions", {"intent": {}, "plan_hash": "a" * 64, "review_hash": "b" * 64}, "propose"),
    ("PUT", "/api/settings", {"profile_title": "Fleet"}, "admin"),
    ("PUT", "/api/nodes/edge", {"resources": []}, "admin"),
    ("DELETE", "/api/nodes/main", None, "admin"),
    ("POST", "/api/nodes/main/enrollment", {}, "admin"),
    ("PUT", "/api/users/bob", {}, "admin"),
    ("PATCH", "/api/users/alice", {"status": "disabled"}, "admin"),
    ("POST", "/api/users/alice/reissue", {}, "admin"),
    ("POST", "/api/users/alice/reset-traffic", {}, "admin"),
    ("POST", "/api/import/users", {"users": []}, "admin"),
    ("DELETE", "/api/users/alice", None, "admin"),
    ("PUT", "/api/endpoints/main:xhttp", ENDPOINT, "admin"),
    ("DELETE", "/api/endpoints/main:reality", None, "admin"),
    ("GET", "/api/tokens", None, "admin"),
    ("POST", "/api/tokens", {"name": "laptop", "scope": "read"}, "admin"),
    ("DELETE", "/api/tokens/{victim}", None, "admin"),
    ("GET", "/api/backup/devices", None, "admin"),
    ("POST", "/api/backup/devices", {"name": "laptop", "public_key": BACKUP_KEY}, "admin"),
    ("GET", "/api/backup/recovery-key", None, "admin"),
    ("PUT", "/api/backup/recovery-key", {"public_key": BACKUP_KEY}, "admin"),
    (
        "PUT",
        "/api/backup/deployer-state",
        {"cluster_yml": "version: 3\n", "servers_json": "[]", "generation": 0},
        "admin",
    ),
]


@pytest.mark.parametrize("held", [*TOKEN_SCOPES, "root"])
@pytest.mark.parametrize(
    ("method", "path", "body", "needed"), SCOPE_MATRIX, ids=[f"{row[0]} {row[1]}" for row in SCOPE_MATRIX]
)
def test_scope_matrix(fleet: Fleet, method: str, path: str, body: Any, needed: str, held: str) -> None:
    assert _request(f"{fleet.url}/api/users/alice", "PUT", {}, ADMIN_TOKEN)[0] == 200
    assert _request(f"{fleet.url}/api/nodes/main", "PUT", {"resources": []}, ADMIN_TOKEN)[0] == 200
    assert _request(f"{fleet.url}/api/endpoints/main:reality", "PUT", ENDPOINT, ADMIN_TOKEN)[0] == 200
    victim = _mint(fleet, "read", "victim")["id"]
    token = ADMIN_TOKEN if held == "root" else _mint(fleet, held)["token"]

    status, answer = _request(fleet.url + path.format(victim=victim), method, body, token)

    if held == "root" or TOKEN_SCOPES.index(held) >= TOKEN_SCOPES.index(needed):
        assert status == 200, answer
    else:
        assert status == 403 and answer["error"]["code"] == "insufficient_scope"


def test_every_admin_route_declares_a_scope() -> None:
    undeclared = [
        pattern.pattern for _, pattern, scope, _ in ROUTES if pattern.pattern.startswith("/api") and not scope
    ]
    assert undeclared == []


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
    approver = Caller(token_id="0123456789ab", scope="approve")
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


@pytest.mark.parametrize(("scope", "sees"), [("read", False), ("propose", False), ("approve", False), ("admin", True)])
def test_only_admin_reads_the_credentials_that_connect_as_a_user(fleet: Fleet, scope: str, sees: bool) -> None:
    alice = fleet.service.put_user("alice")
    token = _mint(fleet, scope)["token"]

    _status, listed = _request(f"{fleet.url}/api/users", token=token)
    _status, one = _request(f"{fleet.url}/api/users/alice", token=token)

    for record in (listed["users"][0], one):
        assert record["username"] == "alice"
        assert (record["uuid"], record["sub_token"]) == ((alice.uuid, alice.sub_token) if sees else ("", ""))
