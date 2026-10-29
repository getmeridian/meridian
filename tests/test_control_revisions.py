"""Topology revisions and the apply fence in the control service: scopes, the self-approval rule, and CAS."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from meridian.control.client import (
    ControlClient,
    ControlServiceConflict,
    ControlServiceError,
    ControlServiceScopeError,
)
from meridian.control.enrollment import token_sha256
from meridian.control.models import ApplyFenceRecord, RevisionProposal, RevisionResult
from meridian.control.revisions import secret_paths
from meridian.control.server import ControlServer
from meridian.control.service import SCHEMA_VERSION, ControlService
from tests.support.topology import single_exit_intent

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
PLAN = "a" * 64
REVIEW = "b" * 64


@dataclass
class Fleet:
    url: str
    service: ControlService

    def client(self, token: str = ADMIN_TOKEN) -> ControlClient:
        return ControlClient(self.url, token)

    def token(self, scope: str, name: str) -> str:
        with self.client() as root:
            return root.create_token(name, scope).token  # type: ignore[arg-type]


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield Fleet(f"http://127.0.0.1:{server.server_address[1]}", service)
    server.shutdown()
    server.server_close()


def _proposal() -> RevisionProposal:
    return RevisionProposal(intent=single_exit_intent().model_dump(mode="json"), plan_hash=PLAN, review_hash=REVIEW)


def test_the_deployers_root_token_proposes_approves_and_applies_in_one_go(fleet: Fleet) -> None:
    with fleet.client() as root:
        proposed = root.propose_revision(_proposal())
        approved = root.approve_revision(proposed.number)
        applied = root.record_revision_result(proposed.number, RevisionResult(status="applied"))

    assert (proposed.number, proposed.status, proposed.author.name, proposed.author.token_id) == (
        1,
        "proposed",
        "root",
        None,
    )
    assert approved.status == "approved" and approved.approved_by is not None
    assert approved.approved_by.name == "root" and approved.approved_at is not None
    assert applied.status == "applied" and applied.finished_at is not None
    assert applied.intent == single_exit_intent().model_dump(mode="json")
    assert (applied.plan_hash, applied.review_hash) == (PLAN, REVIEW)


def test_numbers_increase_and_the_list_is_newest_first_without_intents(fleet: Fleet) -> None:
    with fleet.client() as root:
        numbers = [root.propose_revision(_proposal()).number for _ in range(3)]
        listed = root.revisions()

    assert numbers == [1, 2, 3]
    assert [item.number for item in listed] == [3, 2, 1]
    assert "intent" not in listed[0].model_dump()


def test_a_proposer_may_not_approve_their_own_revision_without_admin(fleet: Fleet) -> None:
    approver = fleet.token("approve", "ci-bot")
    reviewer = fleet.token("approve", "reviewer")
    with fleet.client(approver) as bot, fleet.client(reviewer) as other:
        proposed = bot.propose_revision(_proposal())
        assert proposed.author.name == "ci-bot" and proposed.author.token_id is not None
        with pytest.raises(ControlServiceScopeError, match="proposed itself"):
            bot.approve_revision(proposed.number)
        approved = other.approve_revision(proposed.number)

    assert approved.approved_by is not None and approved.approved_by.name == "reviewer"


def test_an_admin_token_may_approve_its_own_proposal(fleet: Fleet) -> None:
    with fleet.client(fleet.token("admin", "laptop")) as laptop:
        proposed = laptop.propose_revision(_proposal())
        assert laptop.approve_revision(proposed.number).status == "approved"


@pytest.mark.parametrize(
    ("scope", "allowed"),
    [("read", set()), ("propose", {"propose"}), ("approve", {"propose", "approve"}), ("admin", {"all"})],
)
def test_each_step_needs_its_scope(fleet: Fleet, scope: str, allowed: set[str]) -> None:
    with fleet.client() as root:
        others = root.propose_revision(_proposal()).number
    with fleet.client(fleet.token(scope, "device")) as device:
        assert [item.number for item in device.revisions()] == [others]
        steps = {
            "propose": lambda: device.propose_revision(_proposal()),
            "approve": lambda: device.approve_revision(others),
            "result": lambda: device.record_revision_result(others, RevisionResult(status="failed")),
            "fence": lambda: device.swap_fence(device.fence().version, ApplyFenceRecord(fleet_id=FLEET_ID)),
        }
        for step, call in steps.items():
            if "all" in allowed or step in allowed:
                call()
            else:
                with pytest.raises(ControlServiceScopeError):
                    call()


def test_status_moves_only_forward(fleet: Fleet) -> None:
    with fleet.client() as root:
        number = root.propose_revision(_proposal()).number
        with pytest.raises(ControlServiceConflict, match="not approved"):
            root.record_revision_result(number, RevisionResult(status="applied"))
        root.approve_revision(number)
        with pytest.raises(ControlServiceConflict, match="not proposed"):
            root.approve_revision(number)
        root.record_revision_result(number, RevisionResult(status="failed", detail="cancelled"))
        # A retry after a lost reply is a no-op; a different outcome is refused.
        assert root.record_revision_result(number, RevisionResult(status="failed")).detail == "cancelled"
        with pytest.raises(ControlServiceConflict):
            root.record_revision_result(number, RevisionResult(status="applied"))
        assert root.revision(99) is None


@pytest.mark.parametrize(
    ("intent", "leak"),
    [
        ({"control": {"api_token": "x"}}, "control.api_token"),
        ({"exits": [{"password": "x"}]}, "exits.0.password"),
        ({"note": "-----BEGIN PRIVATE KEY-----\nabc"}, "note"),
        ({"note": "mrd_0123456789ab_" + "0" * 64}, "note"),
    ],
    ids=["token-key", "password-in-list", "pem-value", "api-token-value"],
)
def test_an_intent_with_a_secret_is_refused(fleet: Fleet, intent: dict[str, Any], leak: str) -> None:
    assert leak in secret_paths(intent)
    with fleet.client() as root, pytest.raises(ControlServiceError, match="secret-shaped") as refused:
        root.propose_revision(RevisionProposal(intent=intent, plan_hash=PLAN, review_hash=REVIEW))
    assert not isinstance(refused.value, ControlServiceConflict)
    assert fleet.service.revisions.list_revisions().revisions == []


def test_a_real_topology_intent_is_secret_free() -> None:
    assert secret_paths(single_exit_intent().model_dump(mode="json")) == []


def test_the_fence_swaps_only_from_the_version_read(fleet: Fleet) -> None:
    with fleet.client() as root:
        empty = root.fence()
        assert (empty.version, empty.record, empty.fleet_id) == (0, None, FLEET_ID)
        leased = root.swap_fence(0, ApplyFenceRecord(fleet_id=FLEET_ID, generation=3, lease_holder="laptop:1"))
        assert leased.version == 1 and leased.record is not None and leased.record.generation == 3
        with pytest.raises(ControlServiceConflict, match="changed since it was read"):
            root.swap_fence(0, ApplyFenceRecord(fleet_id=FLEET_ID, lease_holder="desktop:2"))
        assert root.fence().record == leased.record


def test_the_fence_refuses_another_fleet_and_an_unapplied_revision(fleet: Fleet) -> None:
    with fleet.client() as root:
        with pytest.raises(ControlServiceError, match="another fleet"):
            root.swap_fence(0, ApplyFenceRecord(fleet_id="11111111-1111-4111-8111-111111111111"))
        number = root.propose_revision(_proposal()).number
        with pytest.raises(ControlServiceConflict, match="has not been applied"):
            root.swap_fence(0, ApplyFenceRecord(fleet_id=FLEET_ID, revision=number))
        root.approve_revision(number)
        root.record_revision_result(number, RevisionResult(status="applied"))
        assert root.swap_fence(0, ApplyFenceRecord(fleet_id=FLEET_ID, revision=number)).version == 1


def test_a_version_four_database_gains_revisions_and_the_fence(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    db = sqlite3.connect(path)
    db.execute("DROP TABLE revisions")
    db.execute("DROP TABLE apply_fence")
    db.execute("ALTER TABLE users DROP COLUMN traffic_carried_bytes")  # added in version 6
    db.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    db.commit()
    db.close()

    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")

    db = sqlite3.connect(path)
    assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(SCHEMA_VERSION),)
    db.close()
    assert [user.username for user in service.list_users()] == ["alice"]
    assert service.revisions.fence().version == 0
    assert service.revisions.list_revisions().revisions == []
