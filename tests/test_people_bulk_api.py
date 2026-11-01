"""People-only API reviews are read-only and durable receipts contain no secrets."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import pytest

from meridian.control.client import ControlClient, ControlServiceScopeError, add_mutation_listener
from meridian.control.models import UserUpdate
from meridian.control.service import ControlService
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from meridian.core.people_query import PeopleQuery
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.node_commands import command_node_spec
from tests.test_control_people_query import NOW, seed
from tests.test_control_people_query import people_service as people_service


def test_bulk_action_export_preserves_typed_parameters_and_only_serializes_supplied_fields():
    from meridian.core.schema import schema_for

    properties = BulkAction.model_json_schema(mode="validation")["properties"]
    exported = schema_for("bulk-action")
    published = exported["properties"]
    assert published.keys() == properties.keys()
    assert {key: value for key, value in published.items() if key != "tag"} == {
        key: value for key, value in properties.items() if key != "tag"
    }
    assert published["tag"]["type"] == "string" and "pattern" in published["tag"]
    assert exported.get("additionalProperties") is False
    for name in ("bulk-preview", "bulk-receipt", "client-bulk"):
        assert any(value.get("properties") == published for value in schema_for(name)["$defs"].values())
    assert BulkAction(action="extend", days=7).model_dump(mode="json") == {"action": "extend", "days": 7}


def test_api_query_preview_commit_receipt_and_undo_preserve_scope_and_read_boundaries(tmp_path):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        user = admin.put_user("sample")
        admin.update_user("sample", UserUpdate(note="Private sample note", tag="GROUP_A"))
        events = []
        stop = add_mutation_listener(events.append)
        try:
            page = admin.query_people(PeopleQuery(tag="GROUP_A"))
            review = admin.preview_bulk(
                BulkPreviewRequest(action=BulkAction(action="disable"), query=PeopleQuery(tag="GROUP_A"))
            )
            assert events == []
            receipt = admin.commit_bulk(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
            assert len(events) == 1
            assert admin.bulk_receipt(receipt.id) == receipt
            for scope in ("read", "propose", "approve"):
                token = admin.create_token("Sample desk", scope)
                with ControlClient(url, token.token) as lower:
                    with pytest.raises(ControlServiceScopeError):
                        lower.query_people(PeopleQuery())
                    with pytest.raises(ControlServiceScopeError):
                        lower.bulk_receipt(receipt.id)
                    body = httpx.get(url + "/api/events", headers={"Authorization": f"Bearer {token.token}"}).text
                    assert all(
                        value not in body for value in (user.uuid, user.sub_token, "Private sample note", "GROUP_A")
                    )
            assert page.total == 1
            assert all(
                value not in receipt.model_dump_json() for value in (user.uuid, user.sub_token, "Private sample note")
            )
            assert admin.undo_bulk(receipt.id).undone_at is not None
            assert admin.get_user("sample").status == "active"
            assert admin.list_users()[0].note == "Private sample note"
        finally:
            stop()


def test_two_simultaneous_commits_against_the_same_versions_have_one_winner(people_service):
    seed(people_service)
    request = BulkPreviewRequest(action=BulkAction(action="disable"), usernames=["p-a", "p-c"])
    reviews = [people_service.bulk.preview(request) for _ in range(2)]
    barrier = threading.Barrier(2)

    def apply(review):
        from meridian.control.errors import ServiceError

        barrier.wait()
        try:
            return people_service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(apply, reviews))
    assert sum(answer == "stale_people" for answer in answers) == 1
    assert sum(not isinstance(answer, str) for answer in answers) == 1


def test_batch_composes_once_and_preview_counts_only_changed_node_documents(people_service, monkeypatch):
    seed(people_service)
    people_service.put_node_spec("main", command_node_spec())
    people_service.put_node_spec("empty", command_node_spec().model_copy(update={"managed_inbounds": []}))
    before = {node.node_id: node.revision for node in people_service.list_nodes()}
    review = people_service.bulk.preview(
        BulkPreviewRequest(action=BulkAction(action="disable"), usernames=["p-a", "p-c"])
    )
    assert review.changed_nodes == 1
    calls = []
    compose = people_service.bulk.compose
    monkeypatch.setattr(people_service.bulk, "compose", lambda: (calls.append(1), compose())[-1])
    people_service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    assert calls == [1]
    assert people_service.node_status("main").revision == before["main"] + 1
    assert people_service.node_status("empty").revision == before["empty"]


def test_receipt_undo_survives_restarting_the_service(people_service, tmp_path):
    seed(people_service)
    review = people_service.bulk.preview(
        BulkPreviewRequest(action=BulkAction(action="devices", device_limit=0), usernames=["p-a"])
    )
    receipt = people_service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    people_service.close()
    restarted = ControlService(
        tmp_path / "people.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    try:
        assert restarted.bulk.receipt(receipt.id) == receipt
        assert restarted.bulk.undo(receipt.id).undone_at is not None
        assert restarted.get_user("p-a").device_limit is None
    finally:
        restarted.close()
