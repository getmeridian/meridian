"""Connection-key rotation keeps the subscription and operator-owned settings."""

from __future__ import annotations

import json

import pytest

from meridian.control.command_models import ReconcileCreate
from meridian.control.errors import ServiceError
from meridian.control.models import PrivacySettings, UserUpdate
from meridian.control.service import ControlService
from tests.support.node_commands import command_node_spec
from tests.test_user_flow_blocks import service as service


def test_rotation_replaces_both_protocol_keys_keeps_the_url_and_preserves_settings(service: ControlService):
    service.put_user("alice")
    before = service.update_user(
        "alice",
        UserUpdate(
            note="Private note",
            tag="FAMILY",
            device_limit=3,
            traffic_limit_bytes=1000,
            traffic_limit_strategy="month",
            expire_at="2031-01-01T00:00:00Z",
        ),
    )
    old_subscription = service.subscription(before.sub_token).status
    result = service.rotate_credentials("alice", expected_generation=before.credential_generation)
    after = result.user
    assert after.uuid != before.uuid
    assert (after.sub_token, after.user_id) == (before.sub_token, before.user_id)
    assert after.credential_generation == before.credential_generation + 1
    assert result.subscription_preserved
    for field in [
        "status",
        "note",
        "tag",
        "device_limit",
        "traffic_limit_bytes",
        "traffic_limit_strategy",
        "expire_at",
        "created_at",
        "lifetime_traffic_bytes",
        "used_traffic_bytes",
    ]:
        assert getattr(after, field) == getattr(before, field)
    assert service.subscription(before.sub_token).status == old_subscription
    with service._lock:
        document = json.loads(service._db.execute("SELECT document FROM nodes WHERE node_id = 'main'").fetchone()[0])
    vless, hysteria = [
        resource for resource in document["resources"] if resource["kind"] in {"xray_users", "hysteria2_users"}
    ]
    assert vless["users"][0]["uuid"] == hysteria["users"][0]["auth"] == after.uuid
    assert result.flow_blocks.nodes[0].reason == "credentials"


def test_a_stale_generation_cannot_rotate_twice(service: ControlService):
    before = service.put_user("alice")
    first = service.rotate_credentials("alice", expected_generation=before.credential_generation)
    count = service._db.total_changes
    with pytest.raises(ServiceError, match="changed") as caught:
        service.rotate_credentials("alice", expected_generation=before.credential_generation)
    assert caught.value.code == "credentials_changed"
    assert service.get_user("alice") == first.user
    assert service._db.total_changes == count


def test_rotation_keeps_private_device_registrations_while_reissue_clears_them(service: ControlService):
    before = service.put_user("alice")
    service.set_privacy(PrivacySettings(device_limits="private"), author="root")
    service.subscription(before.sub_token, user_agent="v2RayTun/1.0", device_headers={"x-hwid": "test-device-0001"})
    registrations = service._store.rows("SELECT * FROM devices")
    assert len(registrations) == 1
    service.rotate_credentials("alice", expected_generation=before.credential_generation)
    assert [tuple(row) for row in service._store.rows("SELECT * FROM devices")] == [tuple(row) for row in registrations]
    reissued = service.reissue_user("alice")
    assert reissued.sub_token != before.sub_token
    assert service._store.rows("SELECT * FROM devices") == []


def test_rotation_can_queue_one_revision_gated_core_cut_off_without_changing_the_url(service: ControlService):
    service.put_node_spec("main", command_node_spec())
    before = service.put_user("alice")
    result = service.rotate_credentials("alice", expected_generation=1, cut_off=True)
    assert result.user.sub_token == before.sub_token
    assert len(result.commands) == 1
    command = result.commands[0]
    assert command.command.kind == "restart_unit" and command.status == "waiting"
    assert command.wait_for.revision == service.node_status("main").revision


def test_a_full_cut_off_queue_rolls_back_rotation_and_its_generation(service: ControlService):
    service.put_node_spec("main", command_node_spec())
    before = service.put_user("alice")
    for _ in range(64):
        service.issue_command("main", ReconcileCreate())
    events = service.events.list()
    revision = service.node_status("main").revision
    with pytest.raises(ServiceError) as failure:
        service.rotate_credentials("alice", expected_generation=1, cut_off=True)
    assert failure.value.code == "command_queue_full"
    assert service.get_user("alice") == before
    assert service.node_status("main").revision == revision
    assert service.events.list() == events
