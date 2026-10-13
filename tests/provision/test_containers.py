"""Tests for provision/containers.py shared Docker Compose lifecycle.

MockConnection succeeds on every unmatched command, so each case registers only its failures.
"""

from __future__ import annotations

import pytest

from meridian.provision.containers import EnvFile, deploy_compose_stack
from tests.support.mock_connection import MockConnection

SAMPLE_COMPOSE = """\
services:
  app:
    image: example:latest
    restart: unless-stopped
"""


def _deploy(conn: MockConnection, **kwargs):
    return deploy_compose_stack(conn, "/opt/test", SAMPLE_COMPOSE, **{"pull_retries": 1, **kwargs})


def _env_writes(conn: MockConnection) -> list[str]:
    # put_text writes .env via "cat > /opt/test/.env" in MockConnection
    return [c for c in conn.calls if ".env" in c]


def test_success_runs_the_full_sequence_without_env_files() -> None:
    conn = MockConnection()

    result = _deploy(conn)

    assert result.changed is True
    assert result.detail == ""
    conn.assert_called_with_pattern("mkdir -p")
    conn.assert_called_with_pattern("docker compose pull")
    conn.assert_called_with_pattern("docker compose up -d")
    assert _env_writes(conn) == []


@pytest.mark.parametrize(
    ("rules", "kwargs", "fragments", "logs"),
    [
        ([("mkdir", 1, "")], {}, ["failed to create"], ""),
        ([("mkdir", 0, ""), ("chmod", 0, ""), ("docker-compose.yml", 1, "")], {}, ["docker-compose.yml"], ""),
        (
            [("docker compose pull", 1, "")],
            {"pull_retries": 2, "pull_retry_delay": 0},
            ["docker compose pull failed"],
            "",
        ),
        (
            [("docker compose up", 1, ""), ("docker compose logs", 0, "ERROR: something broke")],
            {},
            ["docker compose up failed"],
            "ERROR: something broke",
        ),
        (
            [("docker compose logs", 0, "timeout logs")],
            {
                "health_check": lambda: False,
                "health_timeout": 0.1,
                "health_interval": 0.05,
                "service_name": "test-svc",
            },
            ["did not become healthy", "test-svc"],
            "",
        ),
    ],
    ids=["mkdir", "compose-write", "pull", "up", "health"],
)
def test_failures_return_unchanged(rules, kwargs: dict, fragments: list[str], logs: str) -> None:
    conn = MockConnection()
    for pattern, rc, stdout in rules:
        conn.when(pattern, rc=rc, stdout=stdout, stderr="boom" if rc else "")

    result = _deploy(conn, **kwargs)

    assert result.changed is False
    for fragment in fragments:
        assert fragment in result.detail
    assert logs in result.logs


def test_passing_health_check_counts_as_changed() -> None:
    assert _deploy(MockConnection(), health_check=lambda: True, health_timeout=5).changed is True


@pytest.mark.parametrize(
    ("kwargs", "minimum"),
    [
        ({"env_content": "KEY=value\n"}, 1),
        (
            {
                "env_files": [
                    EnvFile(filename=".env", content="KEY=val\n"),
                    EnvFile(filename=".env.sub", content="SUB=1\n"),
                ]
            },
            2,
        ),
    ],
    ids=["env-content", "env-files"],
)
def test_env_files_are_written(kwargs: dict, minimum: int) -> None:
    conn = MockConnection()

    assert _deploy(conn, **kwargs).changed is True
    assert len(_env_writes(conn)) >= minimum


def test_extra_dirs_created() -> None:
    conn = MockConnection()

    assert _deploy(conn, dirs=["/opt/test/data", "/opt/test/certs"]).changed is True
    # work_dir + 2 extra dirs
    assert len([c for c in conn.calls if "mkdir" in c]) == 3


def test_stop_first_runs_down() -> None:
    conn = MockConnection()

    assert _deploy(conn, stop_first=True).changed is True
    conn.assert_called_with_pattern("docker compose down")
