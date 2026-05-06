"""Tests for the localhost executable Studio Engine API."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from meridian.cluster import ClusterConfig
from meridian.core.deploy import DeployRequest, DeployResult
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.engine.api import create_engine_app
from meridian.engine.operations import ActiveDeployOperationError, EngineOperation, OperationManager
from meridian.engine.servers import KeyMaterial
from meridian.ssh import CommandResult


def _client(app_port: int = 8765, **kwargs: object) -> TestClient:
    app = create_engine_app(assets_dir=None, host="127.0.0.1", port=app_port, csrf_token="test-csrf", **kwargs)
    return TestClient(app, base_url=f"http://127.0.0.1:{app_port}")


def test_engine_health_exposes_csrf_and_disables_api_cache() -> None:
    client = _client()

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert response.json()["csrf_token"] == "test-csrf"
    assert response.json()["assets"]["available"] is False
    assert "path" not in response.json()["assets"]


def test_engine_rejects_untrusted_host_origin_and_missing_csrf() -> None:
    client = _client()

    assert client.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/v1/health", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/v1/deploy/dry-run", json={"ip": "198.51.100.10"}).status_code == 403


def test_engine_exposes_contracts_workflows_and_saved_servers() -> None:
    profile = profile_from_draft(
        ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
    )

    class Store:
        def list(self) -> list[object]:
            return [profile]

        def find(self, _query: str) -> object | None:
            return None

    client = _client(server_store_factory=Store)

    schemas = client.get("/api/v1/schemas").json()
    workflows = client.get("/api/v1/workflows").json()
    servers = client.get("/api/v1/servers").json()

    assert any(item["name"] == "deploy-request" for item in schemas["schemas"])
    assert [item["id"] for item in workflows["workflows"]] == ["server-onboarding", "deploy"]
    assert workflows["plans"]["server-onboarding"]["ready_request_schema"] == "server-connection-draft"
    assert servers["servers"] == [
        {
            "auth_state": "unknown",
            "host": "198.51.100.10",
            "id": profile.id,
            "last_error": "",
            "last_validated_at": "",
            "source": "manual",
            "ssh_port": 2222,
            "ssh_user": "ubuntu",
            "title": "Family VPN",
        }
    ]


def test_engine_deploy_dry_run_uses_core_planner() -> None:
    client = _client(cluster_loader=ClusterConfig)

    response = client.post(
        "/api/v1/deploy/dry-run",
        json={"ip": "198.51.100.10"},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.json()["schema"] == "meridian.deploy-dry-run/v1"
    assert response.json()["plan"]["server_ip"] == "198.51.100.10"
    assert response.json()["plan"]["mode"] == "first_deploy"


def test_engine_deploy_dry_run_resolves_saved_server_reference() -> None:
    profile = profile_from_draft(
        ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
    )

    class Store:
        def list(self) -> list[object]:
            return [profile]

        def find(self, query: str) -> object | None:
            return profile if query in {profile.id, profile.title, profile.host} else None

    client = _client(cluster_loader=ClusterConfig, server_store_factory=Store)

    response = client.post(
        "/api/v1/deploy/dry-run",
        json={"requested_server": profile.id},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["server_ip"] == "198.51.100.10"


def test_engine_deploy_start_runs_operation_and_replays_events() -> None:
    def runner(request: DeployRequest, operation: EngineOperation) -> dict[str, Any]:
        operation.add_event(
            {
                "schema": "meridian.event/v1",
                "operation_id": "child-op",
                "seq": 1,
                "time": "2026-05-04T21:00:00Z",
                "level": "info",
                "type": "command.started",
                "phase": "deploy",
                "message": "Deploy started",
                "data": {"password": "not-written"},
            }
        )
        result = DeployResult(
            mode="first_deploy",
            server_ip=request.ip,
            ssh_user=request.user,
            ssh_port=request.ssh_port,
            domain="",
            sni="www.microsoft.com",
            client_name="default",
            harden=True,
            pq=False,
            warp=False,
            geo_block=True,
            panel_url="https://198.51.100.10:9443",
            panel_secret_path="/secret",
            connection_page_path="/join",
            node_count=1,
            relay_count=0,
            summary="Deploy completed for 198.51.100.10",
        )
        return {"schema": "meridian.deploy-operation-result/v1", "result": result.model_dump(mode="json")}

    client = _client(deploy_runner=runner)

    response = client.post(
        "/api/v1/deploy/start",
        json={"ip": "198.51.100.10", "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    operation_id = response.json()["operation"]["id"]
    result_payload = _wait_for_operation(client, operation_id)
    events = client.get(f"/api/v1/operations/{operation_id}/events").json()["events"]
    operations = client.get("/api/v1/operations").json()["operations"]
    later_events = client.get(f"/api/v1/operations/{operation_id}/events?after_seq=1").json()["events"]
    diagnostics = client.get(f"/api/v1/operations/{operation_id}/diagnostics").json()

    assert result_payload["operation"]["state"] == "succeeded"
    assert result_payload["operation"]["last_seq"] >= 1
    assert result_payload["operation"]["warning_count"] == 0
    assert result_payload["operation"]["latest_event"]["operation_id"] == operation_id
    assert result_payload["result"]["result"]["server_ip"] == "198.51.100.10"
    assert operations[0]["id"] == operation_id
    replayed = next(event for event in events if event["message"] == "Deploy started")
    assert replayed["operation_id"] == operation_id
    assert replayed["data"]["password"] == "[redacted]"
    assert all(event["seq"] > 1 for event in later_events)
    assert diagnostics["operation"]["id"] == operation_id
    assert "Meridian Studio Operation Diagnostics" in diagnostics["markdown"]
    assert "not-written" not in str(diagnostics)


def test_engine_rejects_duplicate_running_deploy_for_same_target() -> None:
    release = Event()

    def runner(request: DeployRequest, _operation: EngineOperation) -> dict[str, Any]:
        assert release.wait(timeout=2)
        return _deploy_operation_result(request, summary="done")

    client = _client(deploy_runner=runner)

    first = client.post(
        "/api/v1/deploy/start",
        json={"ip": "198.51.100.10", "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    second = client.post(
        "/api/v1/deploy/start",
        json={"ip": "198.51.100.10", "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    release.set()
    _wait_for_operation(client, first.json()["operation"]["id"])

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["operation"]["id"] == first.json()["operation"]["id"]


def test_engine_rejects_duplicate_saved_server_deploy_by_resolved_target() -> None:
    profile = profile_from_draft(
        ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
    )
    release = Event()

    class Store:
        def list(self) -> list[object]:
            return [profile]

        def find(self, query: str) -> object | None:
            return profile if query in {profile.id, profile.title, profile.host} else None

    def runner(request: DeployRequest, _operation: EngineOperation) -> dict[str, Any]:
        assert release.wait(timeout=2)
        return _deploy_operation_result(request, summary="done")

    client = _client(server_store_factory=Store, deploy_runner=runner)

    first = client.post(
        "/api/v1/deploy/start",
        json={"requested_server": profile.id, "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    second = client.post(
        "/api/v1/deploy/start",
        json={"requested_server": profile.title, "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    release.set()
    _wait_for_operation(client, first.json()["operation"]["id"])

    assert first.status_code == 200
    assert first.json()["operation"]["request_target"] == "direct:198.51.100.10:ubuntu:2222"
    assert second.status_code == 409
    assert second.json()["detail"]["operation"]["id"] == first.json()["operation"]["id"]


def test_operation_manager_rejects_duplicate_target_inside_registry_lock() -> None:
    release = Event()
    manager = OperationManager(max_workers=1)
    request = DeployRequest(ip="198.51.100.10", yes=True)

    def runner(request: DeployRequest, _operation: EngineOperation) -> dict[str, Any]:
        assert release.wait(timeout=2)
        return _deploy_operation_result(request, summary="done")

    first = manager.start_deploy(request, runner, request_target="direct:198.51.100.10:root:22")
    with pytest.raises(ActiveDeployOperationError) as exc_info:
        manager.start_deploy(request, runner, request_target="direct:198.51.100.10:root:22")

    release.set()

    assert exc_info.value.operation.id == first.id


def test_engine_cancelled_operation_shows_result_if_child_completed() -> None:
    started = Event()
    release = Event()

    def runner(request: DeployRequest, _operation: EngineOperation) -> dict[str, Any]:
        started.set()
        assert release.wait(timeout=2)
        return _deploy_operation_result(request, summary="finished after cancel")

    client = _client(deploy_runner=runner)

    response = client.post(
        "/api/v1/deploy/start",
        json={"ip": "198.51.100.10", "yes": True},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    operation_id = response.json()["operation"]["id"]
    assert started.wait(timeout=2)
    cancel_response = client.post(
        f"/api/v1/operations/{operation_id}/cancel",
        json={},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    release.set()
    result_payload = _wait_for_operation(client, operation_id)

    assert cancel_response.status_code == 200
    assert result_payload["operation"]["state"] == "completed_after_cancel"
    assert result_payload["operation"]["has_result"] is True
    assert result_payload["result"]["result"]["summary"] == "finished after cancel"


def test_engine_deploy_start_requires_confirmation() -> None:
    client = _client(deploy_runner=lambda _request, _operation: {})

    response = client.post(
        "/api/v1/deploy/start",
        json={"ip": "198.51.100.10", "yes": False},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Deploy needs confirmation."


def test_engine_saves_and_validates_server_profiles() -> None:
    class Store:
        def __init__(self) -> None:
            self.profiles = []

        def list(self) -> list[object]:
            return self.profiles

        def find(self, query: str) -> object | None:
            return next(
                (
                    profile
                    for profile in self.profiles
                    if profile.id == query or profile.title == query or profile.host == query
                ),
                None,
            )

        def upsert(self, profile: object) -> None:
            self.profiles = [existing for existing in self.profiles if existing.id != profile.id]
            self.profiles.append(profile)

    class Connection:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run(
            self,
            command: str,
            timeout: int = 30,
            *,
            sudo: bool | None = None,
            sensitive: bool = False,
        ) -> CommandResult:
            self.commands.append(command)
            if command == "sudo -n true":
                return CommandResult(args=[], returncode=0)
            if "os-release" in command:
                return CommandResult(args=[], returncode=0, stdout="ubuntu 24.04\n")
            return CommandResult(args=[], returncode=0, stdout="meridian-ok\n")

    store = Store()
    connection = Connection()
    client = _client(
        server_store_factory=lambda: store,
        server_connection_factory=lambda _profile, identity_file="", password="": connection,
    )

    add_response = client.post(
        "/api/v1/servers",
        json={"title": "Family VPN", "host": "198.51.100.10", "ssh_user": "ubuntu", "ssh_port": 2222},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    validate_response = client.post(
        "/api/v1/servers/validate",
        json={"server_ref": "Family VPN"},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert add_response.status_code == 200
    assert add_response.json()["server"]["source"] == "engine"
    assert validate_response.status_code == 200
    result = validate_response.json()["result"]
    assert result["auth_ok"] is True
    assert result["sudo_ok"] is True
    assert result["detected_os"] == "ubuntu 24.04"
    assert result["server"]["auth_state"] == "validated"
    assert "key_path" not in result["server"]


def test_engine_server_validation_failure_returns_friendly_result() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10", ssh_user="ubuntu"))

    class Store:
        def __init__(self) -> None:
            self.profile = profile

        def list(self) -> list[object]:
            return [self.profile]

        def find(self, _query: str) -> object | None:
            return self.profile

        def upsert(self, saved: object) -> None:
            self.profile = saved

    class Connection:
        def run(
            self,
            command: str,
            timeout: int = 30,
            *,
            sudo: bool | None = None,
            sensitive: bool = False,
        ) -> CommandResult:
            return CommandResult(
                args=[],
                returncode=255,
                stderr="ubuntu@198.51.100.10: Permission denied (publickey,password).",
            )

    store = Store()
    client = _client(
        server_store_factory=lambda: store,
        server_connection_factory=lambda _profile, identity_file="", password="": Connection(),
    )

    response = client.post(
        "/api/v1/servers/validate",
        json={"server_ref": "Edge"},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["reachable"] is True
    assert result["auth_ok"] is False
    assert result["server"]["auth_state"] == "failed"
    assert any("ssh-copy-id" in hint for hint in result["hints"])


def test_engine_server_validation_sudo_failure_is_not_deploy_ready() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10", ssh_user="ubuntu"))

    class Store:
        def __init__(self) -> None:
            self.profile = profile

        def list(self) -> list[object]:
            return [self.profile]

        def find(self, _query: str) -> object | None:
            return self.profile

        def upsert(self, saved: object) -> None:
            self.profile = saved

    class Connection:
        def run(
            self,
            command: str,
            timeout: int = 30,
            *,
            sudo: bool | None = None,
            sensitive: bool = False,
        ) -> CommandResult:
            if command == "sudo -n true":
                return CommandResult(args=[], returncode=1, stderr="sudo: a password is required")
            if "os-release" in command:
                return CommandResult(args=[], returncode=0, stdout="ubuntu 24.04\n")
            return CommandResult(args=[], returncode=0, stdout="meridian-ok\n")

    store = Store()
    client = _client(
        server_store_factory=lambda: store,
        server_connection_factory=lambda _profile, identity_file="", password="": Connection(),
    )

    response = client.post(
        "/api/v1/servers/validate",
        json={"server_ref": "Edge"},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["auth_ok"] is True
    assert result["sudo_ok"] is False
    assert result["server"]["auth_state"] == "failed"
    assert "passwordless sudo" in result["server"]["last_error"]


def test_engine_bootstrap_key_installs_generated_public_key() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10", ssh_user="ubuntu"))

    class Store:
        def __init__(self) -> None:
            self.profile = profile

        def list(self) -> list[object]:
            return [self.profile]

        def find(self, _query: str) -> object | None:
            return self.profile

        def upsert(self, saved: object) -> None:
            self.profile = saved

    class Connection:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run(
            self,
            command: str,
            timeout: int = 30,
            *,
            sudo: bool | None = None,
            sensitive: bool = False,
        ) -> CommandResult:
            self.commands.append(command)
            return CommandResult(args=[], returncode=0, stdout="ok\n")

    store = Store()
    identities: list[str] = []
    passwords: list[str] = []
    connections: list[Connection] = []

    def connection_factory(_profile: object, *, identity_file: str = "", password: str = "") -> Connection:
        identities.append(identity_file)
        passwords.append(password)
        connection = Connection()
        connections.append(connection)
        return connection

    client = _client(
        server_store_factory=lambda: store,
        server_connection_factory=connection_factory,
        server_key_provider=lambda: KeyMaterial(
            private_key_path="/tmp/meridian_ed25519",
            public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest meridian-local-engine",
        ),
    )

    with patch("meridian.engine.servers.host_key_known", return_value=True):
        response = client.post(
            "/api/v1/servers/bootstrap-key",
            json={"server_ref": "Edge", "key_policy": "generate_meridian", "password": "one-time-password"},
            headers={"x-meridian-csrf": "test-csrf"},
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["installed"] is True
    assert result["verified"] is True
    assert result["key_path"] == "/tmp/meridian_ed25519"
    assert result["server"]["auth_state"] == "key_ready"
    assert "key_path" not in result["server"]
    assert identities == ["", "/tmp/meridian_ed25519"]
    assert passwords == ["one-time-password", ""]
    assert "authorized_keys" in connections[0].commands[0]


def test_engine_bootstrap_key_rejects_password_before_host_key_is_trusted() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10", ssh_user="ubuntu"))

    class Store:
        def list(self) -> list[object]:
            return [profile]

        def find(self, _query: str) -> object | None:
            return profile

        def upsert(self, _saved: object) -> None:
            raise AssertionError("profile should not be updated before host key trust")

    def connection_factory(_profile: object, *, identity_file: str = "", password: str = "") -> object:
        raise AssertionError("SSH connection should not be opened before host key trust")

    client = _client(server_store_factory=lambda: Store(), server_connection_factory=connection_factory)

    with patch("meridian.engine.servers.host_key_known", return_value=False):
        response = client.post(
            "/api/v1/servers/bootstrap-key",
            json={"server_ref": "Edge", "key_policy": "generate_meridian", "password": "one-time-password"},
            headers={"x-meridian-csrf": "test-csrf"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "SSH host key is not trusted yet"
    assert "verify the server fingerprint" in response.json()["detail"]["hint"]


def test_engine_bootstrap_key_contract_validation_is_readable() -> None:
    client = _client()

    missing_key = client.post(
        "/api/v1/servers/bootstrap-key",
        json={"server_ref": "Edge", "key_policy": "use_existing", "public_key": ""},
        headers={"x-meridian-csrf": "test-csrf"},
    )
    missing_ref = client.post(
        "/api/v1/servers/bootstrap-key",
        json={"server_ref": "", "key_policy": "generate_meridian"},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert missing_key.status_code == 422
    detail = missing_key.json()["detail"]
    assert detail["message"] == "Invalid request body"
    assert "Paste a public key when reusing an existing SSH key." in detail["hint"]
    assert "ValidationError" not in detail["hint"]
    assert missing_ref.status_code == 422
    assert "Choose a saved server or enter a server reference." in missing_ref.json()["detail"]["hint"]


def test_engine_bootstrap_existing_public_key_does_not_claim_verified_without_private_key() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10", ssh_user="ubuntu"))

    class Store:
        def __init__(self) -> None:
            self.profile = profile

        def list(self) -> list[object]:
            return [self.profile]

        def find(self, _query: str) -> object | None:
            return self.profile

        def upsert(self, saved: object) -> None:
            self.profile = saved

    class Connection:
        def run(
            self,
            command: str,
            timeout: int = 30,
            *,
            sudo: bool | None = None,
            sensitive: bool = False,
        ) -> CommandResult:
            return CommandResult(args=[], returncode=0, stdout="ok\n")

    client = _client(
        server_store_factory=lambda: Store(),
        server_connection_factory=lambda _profile, identity_file="", password="": Connection(),
    )

    response = client.post(
        "/api/v1/servers/bootstrap-key",
        json={
            "server_ref": "Edge",
            "key_policy": "use_existing",
            "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexisting user@example",
        },
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["installed"] is True
    assert result["verified"] is False
    assert result["server"]["auth_state"] == "validated"


def test_engine_request_validation_is_human_readable() -> None:
    client = _client()

    response = client.post(
        "/api/v1/servers",
        json={"title": "Edge", "host": "not-an-ip", "ssh_user": "ubuntu", "ssh_port": 22},
        headers={"x-meridian-csrf": "test-csrf"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "Invalid request body"
    assert "host: Enter a valid IP address." in detail["hint"]
    assert "ValidationError" not in detail["hint"]


def test_engine_serves_missing_assets_page_and_static_assets(tmp_path: Path) -> None:
    missing = _client().get("/studio/")
    assert missing.status_code == 200
    assert "Studio assets were not found" in missing.text

    assets = tmp_path / "dist"
    (assets / "studio").mkdir(parents=True)
    (assets / "studio" / "index.html").write_text("studio html", encoding="utf-8")
    app = create_engine_app(assets_dir=assets, host="127.0.0.1", port=8765, csrf_token="test-csrf")
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    response = client.get("/studio/")

    assert response.status_code == 200
    assert "studio html" in response.text


def _wait_for_operation(client: TestClient, operation_id: str) -> dict[str, Any]:
    for _ in range(40):
        payload = client.get(f"/api/v1/operations/{operation_id}/result").json()
        if payload["operation"]["state"] in {"succeeded", "failed", "cancelled", "completed_after_cancel"}:
            return payload
        time.sleep(0.025)
    raise AssertionError("operation did not finish")


def _deploy_operation_result(request: DeployRequest, *, summary: str) -> dict[str, Any]:
    server_ip = request.ip or "198.51.100.10"
    result = DeployResult(
        mode="first_deploy",
        server_ip=server_ip,
        ssh_user=request.user or "root",
        ssh_port=request.ssh_port,
        domain="",
        sni=request.sni or "www.microsoft.com",
        client_name=request.client_name or "default",
        harden=request.harden,
        pq=request.pq,
        warp=request.warp,
        geo_block=request.geo_block,
        panel_url=f"https://{server_ip}:9443",
        panel_secret_path="/secret",
        connection_page_path="/join",
        node_count=1,
        relay_count=0,
        summary=summary,
    )
    return {"schema": "meridian.deploy-operation-result/v1", "result": result.model_dump(mode="json")}
