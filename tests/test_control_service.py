"""Control service: composing signed node documents, long-poll, reports, and subscriptions."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from meridian.control.enrollment import token_sha256
from meridian.control.models import ClientEndpoint, ManagedInbound, NodeSpec, users_resource_id
from meridian.control.server import ControlServer
from meridian.control.service import ControlService, ServiceError
from meridian.core.agent_desired import DesiredNodeState
from meridian.core.agent_protocol import ObservedStateReport, PollRequest
from meridian.core.agent_signing import DESIRED_STATE_CONTEXT, verify
from meridian.core.agent_types import NodeStateRef
from tests.support.agent_node import FakeNode

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
TAG = "meridian-main-reality"


def _spec(*, loglevel: str = "warning") -> NodeSpec:
    archive = hashlib.sha256(b"zip").hexdigest()
    config = {
        "log": {"loglevel": loglevel},
        "inbounds": [{"tag": TAG, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}}],
    }
    return NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "core-bin",
                    "kind": "binary_pin",
                    "name": "xray",
                    "version": "26.7.28",
                    "sources": ["https://example.com/xray.zip"],
                    "size": 100,
                    "sha256": archive,
                    "archive": "zip",
                    "member": "xray",
                    "install_path": "/usr/local/lib/netagent/netcore",
                    "mode": "0755",
                },
                {
                    "id": "core-config",
                    "depends_on": ["core-bin"],
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "config": config,
                    "api_address": "127.0.0.1:10085",
                },
                {
                    "id": "core-unit",
                    "depends_on": ["core-bin", "core-config", users_resource_id(TAG)],
                    "kind": "systemd_unit",
                    "name": "netcore.service",
                    "content": "[Service]\nExecStart=/usr/local/lib/netagent/netcore run\n",
                    "enabled": True,
                    "state": "started",
                    "restart_on": ["core-config", users_resource_id(TAG)],
                },
            ],
            "managed_inbounds": [{"tag": TAG, "protocol": "vless", "flow": "xtls-rprx-vision"}],
        }
    )


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    return ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")


def _document(service: ControlService, node_id: str = "main") -> dict[str, Any]:
    response = service.poll(node_id, PollRequest(have=None, wait_seconds=0))
    assert response["desired"] is not None
    return response["desired"]


def test_document_inserts_users_after_the_xray_config_and_verifies(service: ControlService) -> None:
    service.put_user("alice")
    service.put_node_spec("main", _spec())
    document = _document(service)

    assert [item["id"] for item in document["resources"]] == ["core-bin", "core-config", "users:" + TAG, "core-unit"]
    users = document["resources"][2]
    alice = service.get_user("alice")
    assert alice is not None
    assert users["users"] == [{"user_id": alice.user_id, "uuid": alice.uuid, "flow": "xtls-rprx-vision"}]
    authority = service.authority
    assert verify(document, DESIRED_STATE_CONTEXT, authority.service_public_key(), authority.service_key_id)
    DesiredNodeState.model_validate_json(json.dumps(document))


def test_revision_grows_only_when_resources_change(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    assert service.node_status("main").revision == 1
    service.put_node_spec("main", _spec())
    assert service.node_status("main").revision == 1
    service.put_user("alice")
    assert service.node_status("main").revision == 2
    service.put_user("alice")
    service.put_node_spec("main", _spec(loglevel="error"))
    assert service.node_status("main").revision == 3


def test_poll_with_a_current_document_waits_then_answers_empty(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    status = service.node_status("main")
    have = NodeStateRef(fleet_epoch=1, revision=status.revision or 1, sha256=status.sha256 or "")
    started = time.monotonic()
    response = service.poll("main", PollRequest(have=have, wait_seconds=1))
    assert response["desired"] is None
    assert time.monotonic() - started >= 0.9


def test_a_waiting_poll_wakes_when_the_document_changes(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    status = service.node_status("main")
    have = NodeStateRef(fleet_epoch=1, revision=1, sha256=status.sha256 or "")
    result: dict[str, Any] = {}
    waiter = threading.Thread(
        target=lambda: result.update(service.poll("main", PollRequest(have=have, wait_seconds=30)))
    )
    waiter.start()
    time.sleep(0.2)
    service.put_user("alice")
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert result["desired"]["revision"] == 2


def test_report_records_the_applied_revision_and_failures(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    document = _document(service)
    report = _report(document, failed="core-unit")
    service.report("main", ObservedStateReport.model_validate(report), report)

    status = service.node_status("main")
    assert status.applied is not None and status.applied.revision == 1
    assert status.agent_version == "0.1.0"
    assert status.failed == ["core-unit"]
    assert status.errors == ["core-unit: unit exited"]


def test_a_database_refuses_another_fleet(tmp_path: Path) -> None:
    ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    with pytest.raises(ServiceError):
        ControlService(tmp_path / "control.db", fleet_id="11111111-2222-4333-8444-555555555555", version="4.0.0")


def test_users_keep_their_credentials_across_puts(service: ControlService) -> None:
    first = service.put_user("alice")
    assert service.put_user("alice") == first


def _endpoints() -> list[ClientEndpoint]:
    return [
        ClientEndpoint(
            remark="Reality · Chrome",
            protocol="reality",
            address="198.51.100.7",
            port=443,
            position=0,
            fingerprint="chrome",
            reality_sni="www.google.com",
            reality_public_key="PBK",
            reality_short_id="abcd",
        ),
        ClientEndpoint(
            remark="XHTTP · Chrome",
            protocol="xhttp",
            address="198.51.100.7",
            port=443,
            position=1,
            fingerprint="chrome",
            tls_sni="198.51.100.7",
            host="198.51.100.7",
            path="xpath",
        ),
        ClientEndpoint(
            remark="Hysteria2 · UDP",
            protocol="hysteria2",
            address="198.51.100.7",
            port=443,
            position=2,
            tls_sni="198.51.100.7",
            salamander_password="SALT",
        ),
    ]


def test_base64_subscription_matches_the_golden_uri_shapes(service: ControlService) -> None:
    user = service.put_user("alice")
    for index, endpoint in enumerate(_endpoints()):
        service.put_endpoint(f"e{index}", endpoint)
    _, body, headers = service.subscription(user.sub_token, "")
    lines = base64.b64decode(body).decode().splitlines()
    host, uuid = "198.51.100.7", user.uuid
    assert lines == [
        f"vless://{uuid}@{host}:443?encryption=none&flow=xtls-rprx-vision&type=tcp&security=reality"
        "&sni=www.google.com&fp=chrome&pbk=PBK&sid=abcd#Reality%20%C2%B7%20Chrome",
        f"vless://{uuid}@{host}:443?encryption=none&type=xhttp&path=%2Fxpath&host={host}&mode=packet-up"
        f"&security=tls&sni={host}&fp=chrome#XHTTP%20%C2%B7%20Chrome",
        f"hysteria2://{uuid}@{host}:443/?obfs=salamander&obfs-password=SALT&sni={host}"
        "&fm=%7B%22udp%22%3A%5B%7B%22type%22%3A%22salamander%22%2C%22settings%22%3A%7B%22password%22%3A%22SALT"
        "%22%7D%7D%5D%7D#Hysteria2%20%C2%B7%20UDP",
    ]
    assert headers["profile-title"] == "base64:" + base64.b64encode(b"Meridian").decode()


def test_xray_json_subscription_is_the_automatic_config_test_runs(service: ControlService) -> None:
    from meridian.xray_client import parse_xray_subscription, proxy_outbounds

    user = service.put_user("alice")
    for index, endpoint in enumerate(_endpoints()):
        service.put_endpoint(f"e{index}", endpoint)
    _, body, _ = service.subscription(user.sub_token, "json")
    config = parse_xray_subscription(body)
    assert [item["tag"] for item in proxy_outbounds(config)] == [
        "MERIDIAN_PROXY",
        "MERIDIAN_PROXY-2",
        "MERIDIAN_PROXY-3",
    ]
    assert "remnawave" not in config
    hysteria = proxy_outbounds(config)[2]
    assert hysteria["streamSettings"]["hysteriaSettings"]["auth"] == user.uuid


def test_an_unknown_subscription_is_not_found(service: ControlService) -> None:
    with pytest.raises(ServiceError) as raised:
        service.subscription("x" * 24, "")
    assert raised.value.status == 404


@pytest.fixture
def http(service: ControlService):
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _request(url: str, method: str = "GET", body: Any = None, token: str = "") -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def test_admin_api_needs_the_admin_token(http: str) -> None:
    assert _request(f"{http}/api/health")[0] == 401
    assert _request(f"{http}/api/health", token="wrong")[0] == 401
    status, body = _request(f"{http}/api/health", token=ADMIN_TOKEN)
    assert status == 200 and body["fleet_id"] == FLEET_ID


def test_agent_round_trip_over_http(http: str, service: ControlService) -> None:
    spec = _spec().model_dump(mode="json")
    assert _request(f"{http}/api/nodes/main", "PUT", spec, ADMIN_TOKEN)[0] == 200
    assert _request(f"{http}/api/nodes/main/enrollment", "POST", {})[0] == 401
    status, grant = _request(f"{http}/api/nodes/main/enrollment", "POST", {}, ADMIN_TOKEN)
    assert status == 200 and grant["ca_sha256"] == _request(f"{http}/api/ca", token=ADMIN_TOKEN)[1]["ca_sha256"]

    node = FakeNode()
    status, enrolled = _request(f"{http}/agent/v1/enroll", "POST", node.enroll_request(grant["enrollment_token"]))
    assert status == 200 and enrolled["type"] == "enrolled"
    node.certificate_pem = enrolled["certificate_pem"]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    status, welcome = _request(f"{http}/agent/v1/hello", "POST", node.hello(stamp))
    assert status == 200 and welcome["type"] == "welcome"
    token = welcome["session_token"]

    status, poll = _request(f"{http}/agent/v1/poll", "POST", {"type": "poll", "have": None, "wait_seconds": 0}, token)
    assert status == 200 and poll["desired"]["revision"] == 1
    status, ack = _request(f"{http}/agent/v1/report", "POST", _report(poll["desired"]), token)
    assert status == 200 and ack == {"type": "report_ack", "applied_revision": 1}
    stats = {
        "type": "stats",
        "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
        "seq": 1,
        "collected_at": stamp,
        "users": [],
    }
    status, stats_ack = _request(f"{http}/agent/v1/stats", "POST", stats, token)
    assert status == 200 and stats_ack == {"type": "stats_ack", "counter_epoch": stats["counter_epoch"], "acked_seq": 1}
    assert _request(f"{http}/api/traffic", token=ADMIN_TOKEN) == (200, {"users": []})
    status, status_body = _request(f"{http}/api/nodes/main", token=ADMIN_TOKEN)
    assert status_body["applied"]["revision"] == 1 and status_body["agent_version"] == "0.1.0"


def test_a_spent_enrollment_token_is_refused_over_http(http: str) -> None:
    _, grant = _request(f"{http}/api/nodes/main/enrollment", "POST", {}, ADMIN_TOKEN)
    body = FakeNode().enroll_request(grant["enrollment_token"])
    assert _request(f"{http}/agent/v1/enroll", "POST", body)[0] == 200
    status, error = _request(f"{http}/agent/v1/enroll", "POST", body)
    assert status == 401 and error["error"]["code"] == "enrollment_token_invalid"


def test_agent_errors_use_the_protocol_envelope(http: str) -> None:
    status, body = _request(f"{http}/agent/v1/poll", "POST", {"type": "poll", "have": None, "wait_seconds": 0}, "x")
    assert status == 401
    assert body["type"] == "error" and body["error"]["code"] == "unauthenticated"


def test_admin_validation_errors_are_400(http: str) -> None:
    status, body = _request(f"{http}/api/nodes/main", "PUT", {"resources": "nope"}, ADMIN_TOKEN)
    assert status == 400 and body["error"]["code"] == "invalid_request"


def _report(document: dict[str, Any], *, failed: str = "") -> dict[str, Any]:
    ref = {"fleet_epoch": document["fleet_epoch"], "revision": document["revision"], "sha256": document["sha256"]}
    now = "2026-10-29T12:00:00Z"
    return {
        "type": "report",
        "fleet_id": document["fleet_id"],
        "node_id": document["node_id"],
        "applied": ref,
        "target": None,
        "resources": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "status": "failed" if item["id"] == failed else "applied",
                "applied_sha256": None,
                "error": {"code": "service_failed", "message": "unit exited", "retryable": True}
                if item["id"] == failed
                else None,
                "changed_at": now,
            }
            for item in document["resources"]
        ],
        "health": {
            "status": "healthy",
            "checks": [],
            "uptime_seconds": 10,
            "load1_milli": 0,
            "memory_available_bytes": 1,
            "disk_free_bytes": 1,
        },
        "versions": {
            "agent": "0.1.0",
            "xray": "26.7.28",
            "realm": None,
            "nginx": None,
            "os_id": "debian",
            "os_version": "13",
            "kernel": "6.1.0",
        },
        "observed_at": now,
    }


def test_managed_inbound_flow_is_part_of_the_spec() -> None:
    assert ManagedInbound(tag=TAG, protocol="vless").flow == ""
