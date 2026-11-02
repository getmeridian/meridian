"""Cut-off reviews protect membership and retain unresolved access revocations."""

from pathlib import Path

import pytest

from meridian.control.models import ServiceSettings, UserCreate, UserUpdate
from meridian.control.service import ControlService
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import serve_tokens, token_request
from tests.test_access_profiles import POLICY, REALITY, configure_profiles, spec
from tests.test_access_profiles import service as service
from tests.test_user_flow_blocks import acknowledge


@pytest.mark.parametrize(
    ("grants", "allowed"),
    [
        (["commands.read"], False),
        (["people.read"], False),
        (["people.private"], False),
        (["people.read", "people.private"], True),
    ],
)
def test_cut_off_review_requires_the_permissions_that_read_effective_access(tmp_path: Path, grants, allowed):
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        fleet.service.create_user(UserCreate(username="person", profiles=["limited"]))
        status, issued = token_request(
            fleet.url + "/api/tokens", "POST", {"name": "Reviewer", "capabilities": grants}, ADMIN_TOKEN
        )
        assert status == 200
        status, body = token_request(fleet.url + "/api/users/person/cut-off", token=issued["token"])
        assert status == (200 if allowed else 403)
        if allowed:
            assert body["nodes"] == [{"node_id": "main", "inbound_tags": ["vless"], "effect": "core_restart"}]
        else:
            assert body["error"]["code"] == "insufficient_scope"
            assert "main" not in str(body) and "vless" not in str(body)


def test_cut_off_after_profile_loss_resets_an_inbound_missing_from_current_users(service: ControlService):
    configure_profiles(service)
    service.create_user(UserCreate(username="person", profiles=["udp"]))
    service.put_settings(
        ServiceSettings.model_validate(
            {**service.settings().to_data(), "access_profiles": {"limited": [REALITY], "udp": []}}
        )
    )
    receipt = service.user_flow_blocks("person").nodes[0]
    assert (receipt.reason, receipt.inbound_tags, receipt.status) == ("access_changed", ["hy2"], "pending_node")
    plan = service.cutoffs.scope("person")
    assert [node.to_data() for node in plan.nodes] == [
        {"node_id": "main", "inbound_tags": ["hy2"], "effect": "inbound_reset"}
    ]
    mutation = service.cut_off_user("person", reissue=True)
    assert len(mutation.commands) == 1
    command = mutation.commands[0]
    assert command.command.kind == "reset_inbound" and command.command.inbound_tag == "hy2"
    assert command.wait_for is not None and command.wait_for.revision >= receipt.required.revision


def test_cut_off_combines_current_grants_with_pending_vless_loss(service: ControlService):
    configure_profiles(service)
    service.create_user(UserCreate(username="person", profiles=["limited"]))
    service.update_user("person", UserUpdate(profiles=["udp"]))
    assert service.user_flow_blocks("person").nodes[0].inbound_tags == ["vless"]
    assert [node.to_data() for node in service.cutoffs.scope("person").nodes] == [
        {"node_id": "main", "inbound_tags": ["hy2", "vless"], "effect": "core_restart"}
    ]


def test_cut_off_uses_fresh_receipt_evidence_and_reading_it_writes_nothing(service: ControlService, monkeypatch):
    from meridian.control.models import AGENT_SEEN_SECONDS

    configure_profiles(service)
    service.put_user("person")
    service.update_user("person", UserUpdate(profiles=["limited"]))
    service.node_seen("main", "0.2.11", capabilities=["user_flow_blocking"])
    acknowledge(service)
    assert service.user_flow_blocks("person").nodes[0].status == "blocked"
    assert service.cutoffs.scope("person").nodes[0].inbound_tags == ["vless"]
    now = service.flow_blocks._clock()
    monkeypatch.setattr(service.flow_blocks, "_clock", lambda: now + AGENT_SEEN_SECONDS)
    before = service._db.total_changes
    assert service.user_flow_blocks("person").nodes[0].status == "pending_node"
    assert service.cutoffs.scope("person").nodes[0].inbound_tags == ["hy2", "vless"]
    assert service._db.total_changes == before
