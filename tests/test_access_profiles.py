"""Membership is enforced and delivered from one policy, without rotating credentials."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.control.errors import ServiceError
from meridian.control.models import (
    ClientEndpoint,
    NodeSpec,
    ServiceSettings,
    UserCreate,
    UserImport,
    UserImportBatch,
    UserUpdate,
)
from meridian.control.node_documents import with_users
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.core.people_bulk import BulkCommitRequest, BulkPreviewRequest
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from tests.support.control_service import FLEET_ID

NOW = "2026-11-01T12:00:00Z"
REALITY = "inbound:main:reality"
HY2 = "inbound:main:hy2"
POLICY = {"limited": [REALITY], "udp": [HY2]}


def spec(*, profiles: bool = False) -> NodeSpec:
    return NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "core-config",
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "api_address": "127.0.0.1:10085",
                    "config": {
                        "inbounds": [{"tag": "vless", "protocol": "vless"}, {"tag": "hy2", "protocol": "hysteria2"}]
                    },
                }
            ],
            "managed_inbounds": [
                {"tag": "vless", "protocol": "vless", **({"inbound_ref": REALITY} if profiles else {})},
                {"tag": "hy2", "protocol": "hysteria2", **({"inbound_ref": HY2} if profiles else {})},
            ],
            **({"access_profiles": POLICY} if profiles else {}),
        }
    )


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    control = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    control.put_node_spec("main", spec())
    control.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://vpn.example.org/access"))
    for index, protocol in enumerate(("reality", "hysteria2")):
        control.put_endpoint(
            protocol,
            ClientEndpoint(
                remark=f"Connection {protocol}",
                protocol=protocol,
                address="198.51.100.10",
                port=443,
                position=index,
                reality_sni="www.example.org",
                reality_public_key="public-key",
                reality_short_id="12345678",
                tls_sni="vpn.example.org",
                salamander_password="test-password",
            ),
        )
    yield control
    control.close()


def configure_profiles(service: ControlService) -> None:
    service.put_node_spec("main", spec(profiles=True))
    for protocol, ref in (("reality", REALITY), ("hysteria2", HY2)):
        endpoint = service.get_endpoint(protocol)
        assert endpoint is not None
        service.put_endpoint(protocol, ClientEndpoint.model_validate({**endpoint.to_data(), "inbound_ref": ref}))
    service.put_settings(ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": POLICY}))


def users_by_tag(service: ControlService) -> dict[str, list[str]]:
    document = json.loads(service._store.row("SELECT document FROM nodes WHERE node_id = 'main'")[0])
    return {
        resource["inbound_tag"]: [user["user_id"] for user in resource["users"]]
        for resource in document["resources"]
        if resource["kind"] in ("xray_users", "hysteria2_users")
    }


def test_default_profile_keeps_node_resources_and_every_subscription_byte_identical(service: ControlService):
    user = service.put_user("person")
    suffixes = ("", "info", "json", "mihomo", "stash", "singbox")
    before = {suffix: service.subscription(user.sub_token, suffix) for suffix in suffixes}
    documents = with_users(spec(), [user])
    configure_profiles(service)
    assert service.get_user("person").profiles == ["default"]
    assert with_users(spec(profiles=True), [user]) == documents
    assert {suffix: service.subscription(user.sub_token, suffix) for suffix in suffixes} == before


def test_membership_loss_removes_only_the_lost_inbound_and_records_flow_blocking(service: ControlService):
    configure_profiles(service)
    person = service.put_user("person")
    other = service.put_user("other")
    changed = service.update_user("person", UserUpdate.model_validate({"profiles": ["limited"]}))
    assert (changed.uuid, changed.sub_token, changed.credential_generation) == (
        person.uuid,
        person.sub_token,
        person.credential_generation,
    )
    assert users_by_tag(service) == {"vless": [other.user_id, person.user_id], "hy2": [other.user_id]}
    receipt = service.user_flow_blocks("person")
    assert [(node.node_id, node.reason, node.inbound_tags, node.status) for node in receipt.nodes] == [
        ("main", "access_changed", ["hy2"], "pending_node")
    ]
    assert service._store.row("SELECT COUNT(*) FROM commands")[0] == 0
    service.update_user("person", UserUpdate.model_validate({"profiles": ["limited", "udp"]}))
    assert service.user_flow_blocks("person").nodes == []
    assert service.put_user("person").profiles == ["limited", "udp"]


@pytest.mark.parametrize("change", ["rotate", "reissue", "import", "re-enable"])
def test_cut_off_keeps_lost_inbounds_when_a_later_change_replaces_the_receipt_reason(
    service: ControlService, change: str
):
    configure_profiles(service)
    person = service.put_user("person")
    if change == "import":
        service.import_users(
            UserImportBatch(
                users=[
                    UserImport(
                        username=person.username,
                        uuid="11111111-2222-4333-8444-555555555555",
                        sub_token=person.sub_token,
                        profiles=["udp"],
                    )
                ],
                replace=True,
            )
        )
    else:
        service.update_user("person", UserUpdate(profiles=["udp"]))
        assert service.user_flow_blocks("person").nodes[0].reason == "access_changed"
        if change == "rotate":
            service.rotate_credentials("person", expected_generation=person.credential_generation)
        elif change == "reissue":
            service.reissue_user("person")
        else:
            service.update_user("person", UserUpdate(status="disabled"))
            service.update_user("person", UserUpdate(status="active"))
    assert users_by_tag(service) == {"vless": [], "hy2": [person.user_id]}
    receipts = service.user_flow_blocks("person").nodes
    assert receipts[0].reason == ("disabled" if change == "re-enable" else "credentials")
    assert receipts[0].status == "pending_node" and "vless" in receipts[0].inbound_tags
    writes = service._db.total_changes
    scope = service.cutoffs.scope("person")
    assert service._db.total_changes == writes
    assert [(node.node_id, node.inbound_tags, node.effect) for node in scope.nodes] == [
        ("main", ["hy2", "vless"], "core_restart")
    ]
    assert service._store.row("SELECT COUNT(*) FROM commands")[0] == 0


@pytest.mark.parametrize("suffix", ["", "info", "json", "mihomo", "stash", "singbox"])
def test_every_delivery_format_and_individual_links_exclude_ungranted_connections(service: ControlService, suffix: str):
    configure_profiles(service)
    person = service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited"]}))
    response = service.subscription(person.sub_token, suffix)
    assert "hysteria2://" not in response.body and "Connection hysteria2" not in response.body
    connections = service.user_connections("person")
    assert connections.connections and {row.protocol for row in connections.connections} == {"reality"}
    page = service.pages.render(person.sub_token, "config.json")
    assert "hysteria2" not in page.body
    preview = service.preview_subscription(SubscriptionPreviewRequest(username="person", suffix="info"))
    assert {row.protocol for row in preview.included} == {"reality"}
    assert [(row.protocol, row.reason) for row in preview.omitted] == [("hysteria2", "access_profile")]


def test_unknown_membership_and_used_profile_removal_are_atomic_refusals(service: ControlService):
    configure_profiles(service)
    person = service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited"]}))
    with pytest.raises(ServiceError, match="retired"):
        service.update_user("person", UserUpdate.model_validate({"profiles": ["retired"]}))
    with pytest.raises(ServiceError, match="limited.*1"):
        service.put_settings(
            ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": {"udp": [HY2]}})
        )
    assert service.get_user("person").profiles == ["limited"]
    assert users_by_tag(service) == {"vless": [person.user_id], "hy2": []}
    for profiles in ([], ["limited", "limited"], None):
        with pytest.raises(ValidationError):
            UserUpdate.model_validate({"profiles": profiles})


@pytest.mark.parametrize("deny_first", [False, True])
def test_effective_access_and_preview_keep_the_same_unique_names_as_delivery(service: ControlService, deny_first: bool):
    configure_profiles(service)
    person = service.create_user(UserCreate(username="person", profiles=["limited"]))
    original = service.get_endpoint("reality")
    service.delete_endpoint("reality")
    for index in (0, 1):
        service.put_endpoint(
            f"entry-{index}",
            ClientEndpoint.model_validate(
                {
                    **original.to_data(),
                    "remark": "Equal names",
                    "position": index,
                    "inbound_ref": "inbound:other:reality" if deny_first and index == 0 else REALITY,
                }
            ),
        )
    connections = service.user_connections(person.username).connections
    preview = service.preview_subscription(SubscriptionPreviewRequest(username=person.username, suffix="info"))
    access = service.access.person(person.username)
    assert [entry.remark for entry in preview.included] == [entry.remark for entry in connections]
    assert [entry.remark for entry in access.connections] == [entry.remark for entry in connections]


def test_profile_edit_recomposes_documents_and_revokes_without_editing_membership(service: ControlService):
    configure_profiles(service)
    person = service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited"]}))
    changed = {"limited": [HY2], "udp": [HY2]}
    service.put_settings(ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": changed}))
    assert service.get_user("person").profiles == ["limited"]
    assert users_by_tag(service) == {"vless": [], "hy2": [person.user_id]}
    assert service.user_flow_blocks("person").nodes[0].inbound_tags == ["vless"]


def test_bulk_set_profiles_freezes_membership_and_policy_and_can_undo(service: ControlService):
    configure_profiles(service)
    service.put_user("person")
    service.put_user("other")
    request = BulkPreviewRequest.model_validate(
        {"usernames": ["person", "other"], "action": {"action": "set-profiles", "profiles": ["limited"]}}
    )
    writes = service._db.total_changes
    preview = service.bulk.preview(request)
    assert service._db.total_changes == writes
    assert preview.changed_nodes == 1 and preview.counts.changed == 2
    assert preview.people[0].access.lost == [HY2]
    assert preview.people[0].cut_off.nodes[0].effect == "inbound_reset"
    service.update_user("other", UserUpdate.model_validate({"profiles": ["udp"]}))
    with pytest.raises(ServiceError, match="changed"):
        service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
    assert service.get_user("person").profiles == ["default"]
    preview = service.bulk.preview(request)
    receipt = service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))
    assert service.get_user("person").profiles == service.get_user("other").profiles == ["limited"]
    service.bulk.undo(receipt.id)
    assert service.get_user("person").profiles == ["default"]
    assert service.get_user("other").profiles == ["udp"]
    preview = service.bulk.preview(request)
    service.put_settings(
        ServiceSettings.model_validate(
            {**service.settings().to_data(), "access_profiles": {"limited": [], "udp": [HY2]}}
        )
    )
    with pytest.raises(ServiceError, match="changed"):
        service.bulk.commit(BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash))


def test_import_preserves_membership_unless_explicitly_supplied(service: ControlService):
    configure_profiles(service)
    person = service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited"]}))
    item = {"username": person.username, "uuid": person.uuid, "sub_token": person.sub_token}
    service.import_users(UserImportBatch.model_validate({"replace": True, "users": [item]}))
    assert service.get_user("person").profiles == ["limited"]
    service.import_users(UserImportBatch.model_validate({"replace": True, "users": [{**item, "profiles": ["udp"]}]}))
    assert service.get_user("person").profiles == ["udp"]


def test_effective_access_explains_grants_and_profile_review_is_read_only_and_stale_checked(service: ControlService):
    from meridian.core.access_profile_views import AccessProfileReviewRequest

    configure_profiles(service)
    service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited"]}))
    view = service.access.person("person")
    assert view.profiles == ["limited"]
    assert [(entry.protocol, entry.granted_by) for entry in view.connections] == [("reality", ["limited"])]
    writes = service._db.total_changes
    changed = {"limited": [HY2], "udp": [HY2]}
    review = service.access.preview(AccessProfileReviewRequest(access_profiles=changed, inbound_refs=[REALITY, HY2]))
    assert service._db.total_changes == writes
    assert [(person.username, person.gained, person.lost) for person in review.people] == [("person", [HY2], [REALITY])]
    assert review.cut_off.nodes[0].effect == "core_restart"
    service.update_user("person", UserUpdate.model_validate({"profiles": ["udp"]}))
    with pytest.raises(ServiceError, match="changed"):
        service.put_settings(
            ServiceSettings.model_validate({**service.settings().to_data(), "access_profiles": changed}),
            expected_access_hash=review.access_hash,
        )
    assert service.settings().access_profiles == POLICY


def test_snapshot_round_trip_preserves_membership_and_enforcement(service: ControlService, tmp_path: Path):
    from meridian.control.models import RecoveryKeyUpdate
    from meridian.control.snapshot import decrypt_snapshot, generate_key_pair

    configure_profiles(service)
    user = service.create_user(UserCreate.model_validate({"username": "person", "profiles": ["limited", "udp"]}))
    key = generate_key_pair()
    service.backups.set_recovery_key(RecoveryKeyUpdate(public_key=key.public))
    opened = decrypt_snapshot(service.snapshot(), [key.secret])
    path = tmp_path / "round-trip.db"
    path.write_bytes(opened.database)
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        current = restored.get_user("person")
        assert current.profiles == ["limited", "udp"]
        assert (current.uuid, current.sub_token) == (user.uuid, user.sub_token)
        assert restored.settings().access_profiles == POLICY
        assert users_by_tag(restored) == {"vless": [user.user_id], "hy2": [user.user_id]}
    finally:
        restored.close()


def test_schema_twenty_three_assigns_default_membership_without_changing_credentials(tmp_path: Path):
    from meridian.core.agent_protocol import ObservedStateReport
    from tests.support.capacity import capacity_report

    path = tmp_path / "old.db"
    moment = datetime.fromisoformat(NOW).timestamp()
    control = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: moment)
    user = control.put_user("person")
    control.put_node_spec("main", NodeSpec(resources=[]))
    control.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
    raw = capacity_report()
    control.report("main", ObservedStateReport.model_validate(raw), raw)
    capacity = control.capacity.view("main")
    assert capacity.hourly
    control.close()
    with sqlite3.connect(path) as database:
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'access_membership_%'"
        ).fetchall():
            database.execute(f"DROP TRIGGER {row[0]}")
        database.execute("DROP TABLE IF EXISTS user_access_memberships")
        database.execute("UPDATE meta SET value = '23' WHERE key = 'schema_version'")
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: moment)
    try:
        current = restored.get_user("person")
        assert current.profiles == ["default"]
        assert restored.capacity.current("main").sample == capacity.sample
        assert restored.capacity.view("main").hourly == capacity.hourly
        assert (current.uuid, current.sub_token, current.user_id) == (user.uuid, user.sub_token, user.user_id)
        assert restored._store.meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        restored.close()
