"""Panel restarts must never rotate secrets PostgreSQL and API tokens depend on."""

from __future__ import annotations

from unittest.mock import patch

from meridian.config import REMNAWAVE_BACKEND_IMAGE, REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE
from meridian.provision.containers import ComposeDeployResult
from meridian.provision.remnawave_panel import DeployRemnawavePanel
from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection

_EXISTING_ENV = """\
# Remnawave Panel environment
DATABASE_URL=postgresql://meridian:olddbpass@remnawave-db:5432/remnawave
APP_SECRET=oldapp
METRICS_PASS=oldmetrics
POSTGRES_PASSWORD=olddbpass
"""

# Remnawave 2.x env: API tokens were signed with JWT_AUTH_SECRET.
_LEGACY_ENV = """\
DATABASE_URL=postgresql://meridian:olddbpass@remnawave-db:5432/remnawave
JWT_AUTH_SECRET=oldauth
JWT_API_TOKENS_SECRET=oldapi
METRICS_PASS=oldmetrics
POSTGRES_PASSWORD=olddbpass
IS_DOCS_ENABLED=false
"""


def _stopped_panel() -> MockConnection:
    conn = MockConnection()
    conn.when("docker inspect", rc=1)
    return conn


def _run(conn: MockConnection) -> tuple[object, dict[str, str]]:
    captured: dict[str, str] = {}

    def fake_deploy(_conn, _work_dir, compose, *, env_files, **_kwargs):
        captured.update({env.filename: env.content for env in env_files})
        captured["docker-compose.yml"] = compose
        return ComposeDeployResult(changed=True)

    with patch("meridian.provision.containers.deploy_compose_stack", side_effect=fake_deploy):
        result = DeployRemnawavePanel().run(conn, ProvisionContext(ip="198.51.100.1"))
    return result, captured


def test_restart_reuses_existing_secrets_and_subscription_token() -> None:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=0)
    conn.when("cat /opt/remnawave/.env.subscription", stdout="REMNAWAVE_API_TOKEN=real-token\n")
    conn.when("cat /opt/remnawave/.env", stdout=_EXISTING_ENV)

    result, env = _run(conn)

    assert result.status == "changed"
    assert "POSTGRES_PASSWORD=olddbpass" in env[".env"]
    assert "APP_SECRET=oldapp" in env[".env"]
    assert "METRICS_PASS=oldmetrics" in env[".env"]
    assert env[".env.subscription"] == "REMNAWAVE_API_TOKEN=real-token\n"


def test_legacy_jwt_auth_secret_becomes_app_secret() -> None:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=0)
    conn.when("cat /opt/remnawave/.env.subscription", stdout="")
    conn.when("cat /opt/remnawave/.env", stdout=_LEGACY_ENV)

    result, env = _run(conn)

    assert result.status == "changed"
    assert "APP_SECRET=oldauth" in env[".env"]
    assert "JWT_" not in env[".env"]
    assert "IS_DOCS_ENABLED" not in env[".env"]
    assert "SWAGGER_PATH" not in env[".env"]


def test_incomplete_existing_env_fails_closed() -> None:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=0)
    conn.when("cat /opt/remnawave/.env", stdout="POSTGRES_PASSWORD=olddbpass\n")

    result, env = _run(conn)

    assert result.status == "failed"
    assert "APP_SECRET" in result.detail
    assert env == {}


def test_database_without_env_fails_closed() -> None:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=1)
    conn.when("test -d /opt/remnawave/data", rc=0)

    result, env = _run(conn)

    assert result.status == "failed"
    assert "refusing to create new database credentials" in result.detail
    assert env == {}


def test_fresh_install_generates_secrets() -> None:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=1)
    conn.when("test -d /opt/remnawave/data", rc=1)

    result, env = _run(conn)

    assert result.status == "changed"
    assert "POSTGRES_PASSWORD=" in env[".env"]
    assert "APP_SECRET=" in env[".env"]
    assert "olddbpass" not in env[".env"]
    assert f"image: {REMNAWAVE_BACKEND_IMAGE}" in env["docker-compose.yml"]
    assert f"image: {REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE}" in env["docker-compose.yml"]
