"""Panel restarts never rotate secrets and never downgrade operator-chosen images."""

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


def _compose(backend: str, subscription_page: str) -> str:
    return (
        "services:\n"
        f"  remnawave:\n    image: {backend}\n"
        "  remnawave-db:\n    image: postgres:17-alpine\n"
        f"  remnawave-subscription-page:\n    image: '{subscription_page}'\n"
    )


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


def _restart_with_compose(compose: str) -> str:
    conn = _stopped_panel()
    conn.when("test -f /opt/remnawave/.env", rc=0)
    conn.when("cat /opt/remnawave/.env.subscription", stdout="")
    conn.when("cat /opt/remnawave/.env", stdout=_EXISTING_ENV)
    conn.when("cat /opt/remnawave/docker-compose.yml", stdout=compose)
    result, env = _run(conn)
    assert result.status == "changed"
    return env["docker-compose.yml"]


def test_restart_keeps_operator_upgraded_images() -> None:
    compose = _restart_with_compose(_compose("remnawave/backend:3.9.1", "remnawave/subscription-page:8.2.0"))

    assert "image: remnawave/backend:3.9.1" in compose
    assert "image: remnawave/subscription-page:8.2.0" in compose


def test_restart_keeps_user_managed_floating_tags() -> None:
    compose = _restart_with_compose(_compose("remnawave/backend:3", "docker.io/remnawave/subscription-page:latest"))

    assert "image: remnawave/backend:3\n" in compose
    assert "image: docker.io/remnawave/subscription-page:latest" in compose


def test_restart_upgrades_images_older_than_the_pins() -> None:
    compose = _restart_with_compose(_compose("remnawave/backend:2.8.0", "remnawave/subscription-page:7.2.6"))

    assert f"image: {REMNAWAVE_BACKEND_IMAGE}" in compose
    assert f"image: {REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE}" in compose


def test_running_panel_below_floor_is_upgraded_not_skipped() -> None:
    conn = MockConnection()
    conn.when("docker inspect", stdout="true\n")
    conn.when("test -f /opt/remnawave/.env", rc=0)
    conn.when("cat /opt/remnawave/.env.subscription", stdout="REMNAWAVE_API_TOKEN=real-token\n")
    conn.when("cat /opt/remnawave/.env", stdout=_LEGACY_ENV)
    conn.when(
        "cat /opt/remnawave/docker-compose.yml",
        stdout=_compose("remnawave/backend:2.8.0", "remnawave/subscription-page:7.2.6"),
    )

    result, env = _run(conn)

    assert result.status == "changed"
    assert "APP_SECRET=oldauth" in env[".env"]
    assert f"image: {REMNAWAVE_BACKEND_IMAGE}" in env["docker-compose.yml"]


def test_running_panel_at_or_above_floor_is_left_alone() -> None:
    conn = MockConnection()
    conn.when("docker inspect", stdout="true\n")
    conn.when(
        "cat /opt/remnawave/docker-compose.yml",
        stdout=_compose("remnawave/backend:3.4.1", "remnawave/subscription-page:8.0.0"),
    )

    result, env = _run(conn)

    assert result.status == "skipped"
    assert env == {}
