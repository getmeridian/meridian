"""Tests for the localhost executable Studio Engine API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from meridian.cluster import ClusterConfig
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.engine.api import create_engine_app
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
