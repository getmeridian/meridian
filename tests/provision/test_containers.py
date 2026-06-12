"""Tests for provision/containers.py shared Docker Compose lifecycle."""

from __future__ import annotations

from meridian.provision.containers import ComposeDeployResult, EnvFile, deploy_compose_stack

from .conftest import MockConnection

SAMPLE_COMPOSE = """\
services:
  app:
    image: example:latest
    restart: unless-stopped
"""


class TestDeployComposeStack:
    """Tests for deploy_compose_stack()."""

    def test_success_full_sequence(self) -> None:
        """Happy path: mkdir -> put_text env/compose -> pull -> up succeeds."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)  # put_text dispatches through run("cat > ...")
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        result = deploy_compose_stack(
            conn, "/opt/test", SAMPLE_COMPOSE, pull_retries=1
        )

        assert result.changed is True
        assert result.detail == ""
        conn.assert_called_with_pattern("mkdir -p")
        conn.assert_called_with_pattern("docker compose pull")
        conn.assert_called_with_pattern("docker compose up -d")

    def test_mkdir_failure_returns_unchanged(self) -> None:
        """If mkdir fails, return changed=False with detail."""
        conn = MockConnection()
        conn.when("mkdir", rc=1, stderr="Permission denied")

        result = deploy_compose_stack(conn, "/opt/test", SAMPLE_COMPOSE)

        assert result.changed is False
        assert "failed to create" in result.detail

    def test_compose_write_failure(self) -> None:
        """If writing docker-compose.yml fails, return changed=False."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("docker-compose.yml", rc=1, stderr="No space left")

        result = deploy_compose_stack(conn, "/opt/test", SAMPLE_COMPOSE)

        assert result.changed is False
        assert "docker-compose.yml" in result.detail

    def test_pull_failure_returns_unchanged(self) -> None:
        """Pull failure returns changed=False with error detail."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=1, stderr="network error")

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            pull_retries=2,
            pull_retry_delay=0,
        )

        assert result.changed is False
        assert "docker compose pull failed" in result.detail
        # Pull retries happen inside conn.run() — MockConnection sees one call
        conn.assert_called_with_pattern("docker compose pull")

    def test_compose_up_failure_collects_logs(self) -> None:
        """If docker compose up fails, logs are collected."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=1, stderr="port conflict")
        conn.when("docker compose logs", stdout="ERROR: something broke")

        result = deploy_compose_stack(
            conn, "/opt/test", SAMPLE_COMPOSE, pull_retries=1
        )

        assert result.changed is False
        assert "docker compose up failed" in result.detail
        assert "ERROR: something broke" in result.logs

    def test_health_check_success(self) -> None:
        """Health check passes on first call."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            pull_retries=1,
            health_check=lambda: True,
            health_timeout=5,
        )

        assert result.changed is True

    def test_health_check_failure_returns_unchanged(self) -> None:
        """Health check timeout returns changed=False."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)
        conn.when("docker compose logs", stdout="timeout logs")

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            pull_retries=1,
            health_check=lambda: False,
            health_timeout=0.1,
            health_interval=0.05,
            service_name="test-svc",
        )

        assert result.changed is False
        assert "did not become healthy" in result.detail
        assert "test-svc" in result.detail

    def test_env_content_written_when_provided(self) -> None:
        """When env_content is provided, .env is written via put_text."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            env_content="KEY=value\n",
            pull_retries=1,
        )

        assert result.changed is True
        # put_text writes .env via "cat > /opt/test/.env" in MockConnection
        env_calls = [c for c in conn.calls if ".env" in c]
        assert len(env_calls) > 0

    def test_env_files_written(self) -> None:
        """EnvFile objects are written via put_text."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            env_files=[
                EnvFile(filename=".env", content="KEY=val\n"),
                EnvFile(filename=".env.sub", content="SUB=1\n"),
            ],
            pull_retries=1,
        )

        assert result.changed is True
        env_calls = [c for c in conn.calls if ".env" in c]
        assert len(env_calls) >= 2

    def test_extra_dirs_created(self) -> None:
        """Extra directories are created alongside work_dir."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            dirs=["/opt/test/data", "/opt/test/certs"],
            pull_retries=1,
        )

        assert result.changed is True
        mkdir_calls = [c for c in conn.calls if "mkdir" in c]
        # work_dir + 2 extra dirs = 3 mkdir calls
        assert len(mkdir_calls) == 3

    def test_no_env_file_when_empty(self) -> None:
        """When env_content is empty and no env_files, .env is not written."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose pull", rc=0)
        conn.when("docker compose up", rc=0)

        deploy_compose_stack(
            conn, "/opt/test", SAMPLE_COMPOSE, pull_retries=1
        )

        env_calls = [c for c in conn.calls if ".env" in c]
        assert len(env_calls) == 0

    def test_stop_first_runs_down(self) -> None:
        """When stop_first=True, docker compose down is called before pull."""
        conn = MockConnection()
        conn.when("mkdir", rc=0)
        conn.when("chmod", rc=0)
        conn.when("cat >", rc=0)
        conn.when("install", rc=0)
        conn.when("mv ", rc=0)
        conn.when("docker compose", rc=0)

        result = deploy_compose_stack(
            conn,
            "/opt/test",
            SAMPLE_COMPOSE,
            pull_retries=1,
            stop_first=True,
        )

        assert result.changed is True
        conn.assert_called_with_pattern("docker compose down")


class TestComposeDeployResult:
    """Tests for ComposeDeployResult dataclass."""

    def test_defaults(self) -> None:
        r = ComposeDeployResult(changed=True)
        assert r.changed is True
        assert r.detail == ""
        assert r.logs == ""

    def test_failure_with_logs(self) -> None:
        r = ComposeDeployResult(changed=False, detail="up failed", logs="error output")
        assert r.changed is False
        assert r.detail == "up failed"
        assert r.logs == "error output"
