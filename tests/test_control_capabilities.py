"""Every catalog capability is exercised over HTTP, including conditional and response grants."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from meridian.control.api_auth import require_route
from meridian.control.client import ControlClient, add_mutation_listener
from meridian.control.errors import ServiceError
from meridian.control.models import RevisionProposal, TokenCreate, UserUpdate
from meridian.control.route_catalog import API_ROUTES, PROTECTED_UI_ROUTES
from meridian.control.snapshot import generate_key_pair
from meridian.control.token_capabilities import capability_catalog, legacy_capabilities
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from meridian.core.token_models import TOKEN_CAPABILITIES, TOKEN_SCOPES, scope_covers
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.support.token_service import Fleet, serve_tokens, token_request
from tests.test_control_tokens import ENDPOINT, ROUTE_SAMPLES


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    with serve_tokens(tmp_path) as result:
        yield result


UI_SAMPLES = {
    "fleet": ("/ui/api/fleet", None),
    "share": ("/ui/api/people/alice/share", None),
    "expiry_preview": ("/ui/api/people/alice/expiry?months=1", None),
}
CAPABILITY_ROUTES = [*API_ROUTES, *PROTECTED_UI_ROUTES]


@pytest.mark.parametrize("held", [*TOKEN_CAPABILITIES, "all"])
@pytest.mark.parametrize("route", CAPABILITY_ROUTES, ids=lambda route: f"{route.method} {route.path}")
def test_capability_matrix(fleet: Fleet, route, held: str) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        root.put_user("alice")
    token_request(fleet.url + "/api/nodes/main", "PUT", {"resources": []}, ADMIN_TOKEN)
    token_request(fleet.url + "/api/endpoints/main:reality", "PUT", ENDPOINT, ADMIN_TOKEN)
    grants = list(TOKEN_CAPABILITIES) if held == "all" else [held]
    status, issued = token_request(
        fleet.url + "/api/tokens", "POST", {"name": "Integration", "capabilities": grants}, ADMIN_TOKEN
    )
    assert status == 200, issued
    status, victim = token_request(fleet.url + "/api/tokens", "POST", {"name": "Victim", "scope": "read"}, ADMIN_TOKEN)
    assert status == 200
    sample = UI_SAMPLES.get(route.operation_id) or ROUTE_SAMPLES[route.operation_id]
    path, body = sample
    if route.operation_id in {"create_token", "preview_token"}:
        body = {"name": "Child", "capabilities": grants}
    status, answer = token_request(fleet.url + path.format(victim=victim["id"]), route.method, body, issued["token"])
    allowed = set(route.capabilities) <= set(grants)
    assert (status not in {401, 403}) == allowed, answer


def test_catalog_has_every_capability_with_native_labels_and_runnable_operations() -> None:
    catalog = capability_catalog()
    assert {row.capability for row in catalog.capabilities} == set(TOKEN_CAPABILITIES)
    for row in catalog.capabilities:
        assert set(row.labels) == {"en", "ru", "fa", "zh"}
        assert all(text.strip() for text in row.labels.values())
        assert row.operations


@pytest.mark.parametrize(
    ("extra", "keys", "private"),
    [(None, False, False), ("people.credentials", True, False), ("people.private", False, True)],
)
def test_credentials_and_private_fields_are_independent_grants(
    fleet: Fleet, extra: str | None, keys: bool, private: bool
) -> None:
    person = fleet.service.put_user("sample")
    fleet.service.update_user("sample", UserUpdate(note="Private note", tag="TRIAL"))
    grants = ["people.read"] + ([extra] if extra else [])
    _, issued = token_request(
        fleet.url + "/api/tokens", "POST", {"name": "Reader", "capabilities": grants}, ADMIN_TOKEN
    )
    _, account = token_request(fleet.url + "/api/users/sample", token=issued["token"])
    assert account["uuid"] == (person.uuid if keys else "")
    assert account["sub_token"] == (person.sub_token if keys else "")
    assert (account["note"], account["tag"]) == (("Private note", "TRIAL") if private else ("", ""))


def test_editing_does_not_return_connection_credentials(fleet: Fleet) -> None:
    _, issued = token_request(
        fleet.url + "/api/tokens", "POST", {"name": "Editor", "capabilities": ["people.edit"]}, ADMIN_TOKEN
    )
    status, account = token_request(fleet.url + "/api/users", "POST", {"username": "sample"}, issued["token"])
    assert status == 200
    assert (account["uuid"], account["sub_token"], account["note"]) == ("", "", "")
    status, account = token_request(fleet.url + "/api/users/sample", "PATCH", {"status": "disabled"}, issued["token"])
    assert status == 200 and account["uuid"] == account["sub_token"] == ""


def test_restart_permission_cannot_queue_other_command_kinds(fleet: Fleet) -> None:
    token_request(fleet.url + "/api/nodes/main", "PUT", {"resources": []}, ADMIN_TOKEN)
    _, issued = token_request(
        fleet.url + "/api/tokens", "POST", {"name": "Restarter", "capabilities": ["commands.restart"]}, ADMIN_TOKEN
    )
    status, _ = token_request(
        fleet.url + "/api/nodes/main/commands", "POST", {"kind": "restart_unit", "unit": "xray"}, issued["token"]
    )
    assert status not in {401, 403}
    status, answer = token_request(
        fleet.url + "/api/nodes/main/commands", "POST", {"kind": "reconcile"}, issued["token"]
    )
    assert status == 403 and "commands.run" in answer["error"]["message"]


def test_cut_off_needs_its_own_capability_and_refuses_before_rotation(fleet: Fleet) -> None:
    person = fleet.service.put_user("sample")
    _, issued = token_request(
        fleet.url + "/api/tokens", "POST", {"name": "Rotator", "capabilities": ["people.reissue"]}, ADMIN_TOKEN
    )
    status, _ = token_request(fleet.url + "/api/users/sample/reissue", "POST", {"cut_off": True}, issued["token"])
    assert status == 403
    assert fleet.service.get_user("sample").uuid == person.uuid


def test_explicit_approval_does_not_approve_its_own_proposal(fleet: Fleet) -> None:
    _, issued = token_request(
        fleet.url + "/api/tokens",
        "POST",
        {"name": "Reviewer", "capabilities": ["revisions.propose", "revisions.approve"]},
        ADMIN_TOKEN,
    )
    caller = fleet.service.tokens.authenticate(issued["token"])
    proposal = fleet.service.revisions.propose(
        RevisionProposal(intent={}, plan_hash="a" * 64, review_hash="b" * 64), caller=caller
    )
    status, answer = token_request(fleet.url + f"/api/revisions/{proposal.number}/approve", "POST", {}, issued["token"])
    assert status == 403 and "revisions.approve_own" in answer["error"]["message"]


def test_backup_read_only_downloads_ciphertext_and_cannot_register_a_key(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        root.register_device_key("Managing machine", generate_key_pair().public)
        reader = root.create_token("Snapshot reader", capabilities=["backup.read"])
    with ControlClient(fleet.url, reader.token) as client:
        encrypted = client.snapshot()
    assert encrypted.startswith(b"meridian-snapshot/v1\n")
    assert b"age-encryption.org/v1\n" in encrypted
    status, _ = token_request(
        fleet.url + "/api/backup/devices",
        "POST",
        {"name": "Other key", "public_key": generate_key_pair().public},
        reader.token,
    )
    assert status == 403


def test_preview_creates_no_token_event_or_backup_pull(fleet: Fleet) -> None:
    reported = []
    stop = add_mutation_listener(reported.append)
    try:
        before = fleet.service.tokens.list_tokens()
        with ControlClient(fleet.url, ADMIN_TOKEN) as root:
            preview = root.preview_token(TokenCreate(name="Reader", capabilities=["people.read"]))
        assert preview.capabilities == ["people.read"] and preview.expires_at is not None
        assert preview.permissions[0].labels["ru"]
        assert fleet.service.tokens.list_tokens() == before
        assert reported == []
    finally:
        stop()


@pytest.mark.parametrize(
    "body",
    [
        {"capabilities": []},
        {"capabilities": ["*"]},
        {"scope": "read", "capabilities": ["people.read"]},
        {"scope": "read", "expires_in_days": None},
        {"capabilities": ["people.read"], "profile_id": "trial"},
    ],
)
def test_invalid_or_unenforced_grants_never_issue(fleet: Fleet, body: dict) -> None:
    status, _ = token_request(fleet.url + "/api/tokens", "POST", {"name": "Integration", **body}, ADMIN_TOKEN)
    assert status == 400
    assert fleet.service.tokens.list_tokens().tokens == []


@pytest.mark.parametrize("scope", TOKEN_SCOPES)
def test_legacy_tokens_keep_expiry_and_frozen_equivalent_grants(fleet: Fleet, tmp_path: Path, scope: str) -> None:
    _, issued = token_request(fleet.url + "/api/tokens", "POST", {"name": "Legacy", "scope": scope}, ADMIN_TOKEN)
    with sqlite3.connect(tmp_path / "control.db") as db:
        db.execute("UPDATE api_tokens SET capabilities = NULL, expires_at = NULL WHERE token_id = ?", (issued["id"],))
    upgraded = type(fleet.service)(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=fleet.clock)
    caller = upgraded.tokens.authenticate(issued["token"])
    assert caller.capabilities == legacy_capabilities(scope)
    assert upgraded.tokens.get(issued["id"]).expires_at is None
    for route in CAPABILITY_ROUTES:
        expected = "root" not in route.capabilities and any(scope_covers(scope, needed) for needed in route.scopes)
        try:
            require_route(caller, route)
            allowed = True
        except ServiceError as exc:
            assert exc.code == "insufficient_scope"
            allowed = False
        assert allowed == expected, (scope, route.path)
    with sqlite3.connect(tmp_path / "control.db") as db:
        stored = json.loads(
            db.execute("SELECT capabilities FROM api_tokens WHERE token_id = ?", (issued["id"],)).fetchone()[0]
        )
    assert stored == sorted(caller.capabilities)


def test_bulk_and_query_grants_do_not_grant_credential_import(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        token = root.create_token("People batch", capabilities=["people.query", "people.batch", "people.private"])
    status, page = token_request(fleet.url + "/api/people", token=token.token)
    assert status == 200 and page["users"] == []
    status, answer = token_request(fleet.url + "/api/import/users", "POST", {"users": []}, token.token)
    assert status == 403 and answer["error"]["code"] == "insufficient_scope"


def test_bulk_tokens_keep_reviewed_action_parameters_and_can_follow_receipts(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        root.put_user("sample")
        token = root.create_token("People batch", capabilities=["people.batch", "people.private"])
        with ControlClient(fleet.url, token.token) as client:
            preview = client.preview_bulk(BulkPreviewRequest(action=BulkAction(action="disable"), usernames=["sample"]))
            assert preview.action.to_data() == {"action": "disable"}
            receipt = client.commit_bulk(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
            assert client.bulk_receipt(receipt.id) == receipt
            assert root.get_user("sample").status == "disabled"
            assert client.undo_bulk(receipt.id).undone_at is not None
            assert root.get_user("sample").status == "active"


def test_a_delegated_token_cannot_outlive_or_exceed_its_issuer(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        parent = root.create_token("Issuer", capabilities=["tokens.issue", "people.read"], expires_in_days=1)
    for request in [
        {"capabilities": ["people.edit"], "expires_in_days": 1},
        {"capabilities": ["people.read"], "expires_in_days": 2},
        {"capabilities": ["people.read"], "never_expires": True},
    ]:
        status, _ = token_request(fleet.url + "/api/tokens", "POST", {"name": "Child", **request}, parent.token)
        assert status == 403
    status, child = token_request(
        fleet.url + "/api/tokens",
        "POST",
        {"name": "Child", "capabilities": ["people.read"], "expires_in_days": 1},
        parent.token,
    )
    assert status == 200 and child["capabilities"] == ["people.read"]


def test_revoked_issuers_cannot_use_a_cached_grant_to_mint(fleet: Fleet) -> None:
    with ControlClient(fleet.url, ADMIN_TOKEN) as root:
        parent = root.create_token("Issuer", capabilities=["tokens.issue", "people.read"], expires_in_days=1)
    caller = fleet.service.tokens.authenticate(parent.token)
    fleet.service.tokens.revoke(parent.id)
    from meridian.control.errors import ServiceError

    with pytest.raises(ServiceError) as refused:
        fleet.service.tokens.issue(
            TokenCreate(name="Child", capabilities=["people.read"], expires_in_days=1), issuer=caller
        )
    assert refused.value.code == "unauthenticated"
