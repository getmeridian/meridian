"""Tests for provision/containers.py shared Docker Compose lifecycle.

MockConnection succeeds on every unmatched command, so each case registers only its failures.
"""

from __future__ import annotations

import pytest

from meridian.provision.containers import EnvFile, compose_error, deploy_compose_stack
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


def test_compose_error_keeps_the_cause_after_a_long_progress_narration() -> None:
    progress = "".join(f" Container ctlsvc-{n}  Creating\n Container ctlsvc-{n}  Created\n" for n in range(20))
    stderr = (
        " Network ctlsvc-net  Creating\n" + progress + "dependency failed to start: container ctlsvc-db is unhealthy\n"
    )

    assert compose_error(stderr) == "dependency failed to start: container ctlsvc-db is unhealthy"


def test_failed_up_reports_container_state_oom_kills_and_the_newest_logs() -> None:
    conn = MockConnection()
    conn.when("docker compose up", rc=1, stderr=" Volume ctlsvc-data  Created\nError: container ctlsvc-db exited (137)")
    conn.when("docker inspect --format", stdout="/ctlsvc-db: exited, exit 137, OOM-killed true, restarts 0\n")
    conn.when("journalctl -k", stdout="Out of memory: Killed process 4242 (postgres)\n")
    conn.when("docker compose logs", stdout="early line\n" * 400 + "ctlsvc-db | FATAL: the last word\n")

    result = _deploy(conn)

    assert "Error: container ctlsvc-db exited (137)" in result.detail
    assert "OOM-killed true" in result.detail
    assert "Killed process 4242 (postgres)" in result.detail
    assert result.detail.rstrip().endswith("FATAL: the last word")
    assert result.started is False


def test_health_timeout_says_the_stack_started_and_why_it_is_not_healthy() -> None:
    conn = MockConnection()
    conn.when(
        "docker inspect --format", stdout="/app: running, exit 0, OOM-killed false, restarts 4, health unhealthy\n"
    )

    result = _deploy(conn, health_check=lambda: False, health_timeout=0.1, health_interval=0.05)

    assert result.started is True
    assert "restarts 4, health unhealthy" in result.detail


def test_an_up_killed_by_its_timeout_says_so_instead_of_quoting_progress() -> None:
    conn = MockConnection()
    conn.when("docker compose up", rc=124, stderr=" Network ctlsvc_internal  Creating\n Volume ctlsvc_data  Created\n")

    result = _deploy(conn, up_timeout=600)

    assert result.detail.startswith("docker compose up did not finish within 600 s")
    assert "Creating" not in result.detail
