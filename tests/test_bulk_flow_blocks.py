"""Bulk access changes use the same identity-bound flow receipts as person edits."""

import json

import pytest

from meridian.control.models import NodeSpec
from meridian.core.agent_protocol import ObservedStateReport, UserFlowBlocking
from meridian.core.agent_types import NodeStateRef
from tests.test_control_people_bulk import commit, preview
from tests.test_user_flow_blocks import acknowledge
from tests.test_user_flow_blocks import service as service


@pytest.mark.parametrize(
    ("action", "parameters", "reason"),
    [
        ("disable", {}, "disabled"),
        ("delete", {}, "removed"),
        ("expiry", {"expire_at": "2026-11-01T11:59:59Z"}, "expired"),
    ],
)
def test_bulk_access_removal_queues_flow_blocks_for_every_person_and_node(service, action, parameters, reason):
    users = [service.put_user(name) for name in ("p-a", "p-b", "p-c")]
    remaining = service.put_user("p-other")
    spec = service._store.row("SELECT spec FROM nodes WHERE node_id = ?", ("main",))[0]
    service.put_node_spec("second", NodeSpec.model_validate_json(spec))
    for node in ("main", "second"):
        service.node_seen(node, "0.2.11", capabilities=["user_flow_blocking"])
    review = preview(service, action, names=[user.username for user in users], **parameters)
    receipt = commit(service, review)
    assert (receipt.counts.changed, receipt.changed_nodes) == (3, 2)
    for node in ("main", "second"):
        document = json.loads(service._store.row("SELECT document FROM nodes WHERE node_id = ?", (node,))[0])
        carried = [
            person["user_id"]
            for resource in document["resources"]
            if resource["kind"] in {"xray_users", "hysteria2_users"}
            for person in resource["users"]
        ]
        assert carried == [remaining.user_id, remaining.user_id]
    for user in users:
        blocks = service.flow_blocks.read(user).nodes
        assert len(blocks) == 2
        assert {block.node_id for block in blocks} == {"main", "second"}
        assert all(
            (block.credential_generation, block.reason, block.status)
            == (user.credential_generation, reason, "pending_node")
            for block in blocks
        )
        current = service.get_user(user.username)
        if action == "delete":
            assert current is None
        else:
            assert (current.uuid, current.sub_token, current.credential_generation) == (
                user.uuid,
                user.sub_token,
                user.credential_generation,
            )
    assert service._store.row("SELECT COUNT(*) FROM commands")[0] == 0
    before = service._db.total_changes
    assert commit(service, review) == receipt
    assert service._db.total_changes == before
    acknowledge(service)
    report = ObservedStateReport.model_validate_json(
        service._store.row("SELECT report FROM nodes WHERE node_id = ?", ("main",))[0]
    )
    node = service.node_status("second")
    ref = NodeStateRef(fleet_epoch=node.fleet_epoch, revision=node.revision, sha256=node.sha256)
    second_report = report.model_copy(
        update={
            "node_id": "second",
            "applied": ref,
            "target": ref,
            "user_flow_blocking": UserFlowBlocking(document=ref, status="ready", error=None),
        }
    )
    service.report("second", second_report, second_report.model_dump(mode="json"))
    assert all(block.status == "blocked" for user in users for block in service.flow_blocks.read(user).nodes)
    assert service.get_user(remaining.username) == remaining


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("extend", {"days": 3}),
        ("expiry", {"expire_at": "2026-12-04T12:00:00Z"}),
        ("quota", {"traffic_limit_bytes": 100}),
        ("reset-traffic", {}),
        ("enable", {}),
        ("disable", {}),
        ("tag", {"tag": "GROUP_A"}),
        ("devices", {"device_limit": 0}),
        ("renewal", {"renewal_interval_days": 30}),
        ("delete", {}),
    ],
)
def test_bulk_actions_and_undo_never_rotate_credentials(service, action, parameters):
    from meridian.control.models import UserUpdate

    service.put_user("p-a")
    service.rotate_credentials("p-a", expected_generation=1)
    service.update_user("p-a", UserUpdate(expire_at="2026-12-01T12:00:00Z"))
    if action == "enable":
        service.update_user("p-a", UserUpdate(status="disabled"))
    before = service.get_user("p-a")
    rotations = sum(event.kind == "user.credentials_rotated" for event in service.events.list().events)
    receipt = commit(service, preview(service, action, names=["p-a"], **parameters))
    if action != "delete":
        after = service.get_user("p-a")
        assert (after.uuid, after.sub_token, after.credential_generation) == (
            before.uuid,
            before.sub_token,
            before.credential_generation,
        )
        if receipt.undo_until is not None:
            service.bulk.undo(receipt.id)
            restored = service.get_user("p-a")
            assert (restored.uuid, restored.sub_token, restored.credential_generation) == (
                before.uuid,
                before.sub_token,
                before.credential_generation,
            )
    encoded = json.dumps(
        {"receipt": receipt.to_data(), "events": [event.to_data() for event in service.events.list().events]}
    )
    assert before.uuid not in encoded
    assert before.sub_token not in encoded
    assert sum(event.kind == "user.credentials_rotated" for event in service.events.list().events) == rotations


def test_failed_bulk_delete_rolls_back_people_documents_flow_blocks_and_receipt(service, monkeypatch):
    users = [service.put_user(name) for name in ("p-a", "p-b")]
    review = preview(service, "delete", names=[user.username for user in users])
    before_document = service._store.row("SELECT document FROM nodes WHERE node_id = ?", ("main",))[0]
    before_events = service.events.list()
    compose = service.bulk.compose

    def fail_after_composition(*, retired=None):
        compose(retired=retired)
        raise RuntimeError("composition failed")

    monkeypatch.setattr(service.bulk, "compose", fail_after_composition)
    with pytest.raises(RuntimeError, match="composition failed"):
        commit(service, review)
    assert [service.get_user(user.username) for user in users] == users
    assert service._store.row("SELECT document FROM nodes WHERE node_id = ?", ("main",))[0] == before_document
    assert service.events.list() == before_events
    assert all(service.flow_blocks.read(user).nodes == [] for user in users)
    assert service._store.row("SELECT COUNT(*) FROM bulk_receipts")[0] == 0
