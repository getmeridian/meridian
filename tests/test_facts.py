"""Tests for typed server facts."""

from __future__ import annotations

import subprocess

from meridian.facts import ServerFacts, parse_ssh_ports


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, subprocess.CompletedProcess[str]] = {}

    def when(self, pattern: str, *, stdout: str = "", stderr: str = "", rc: int = 0) -> FakeConn:
        self.responses[pattern] = subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)
        return self

    def run(self, command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        for pattern, response in self.responses.items():
            if pattern in command:
                return response
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_parse_ssh_ports_dedupes_and_ignores_invalid_tokens() -> None:
    assert parse_ssh_ports("22\nPort 2222\n99999\n22\n") == [22, 2222]


def test_os_release_parses_quoted_values_and_caches() -> None:
    conn = FakeConn().when(
        "/etc/os-release",
        stdout='ID="ubuntu"\nVERSION_CODENAME="noble"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n',
    )
    facts = ServerFacts(conn)  # type: ignore[arg-type]

    assert facts.os_release().id == "ubuntu"
    assert facts.os_release().version_codename == "noble"
    assert facts.os_release().pretty_name == "Ubuntu 24.04 LTS"
    assert len([c for c in conn.calls if "/etc/os-release" in c]) == 1


def test_ssh_ports_falls_back_to_22() -> None:
    conn = FakeConn()
    facts = ServerFacts(conn)  # type: ignore[arg-type]

    assert facts.ssh_ports() == [22]


def test_docker_state_collects_compose_and_running_container_status() -> None:
    conn = (
        FakeConn()
        .when("docker --version", stdout="Docker version 28.0.0\n")
        .when("docker compose version", stdout="Docker Compose version v2.33.0\n")
        .when("docker ps -q", stdout="abc123\n")
    )
    facts = ServerFacts(conn)  # type: ignore[arg-type]

    state = facts.docker_state()
    assert state.installed is True
    assert state.compose_available is True
    assert state.has_running_containers is True
    assert state.version == "Docker version 28.0.0"


def test_ufw_state_reports_active() -> None:
    conn = FakeConn().when("which ufw", stdout="/usr/sbin/ufw\n").when("ufw status", stdout="Status: active\n")
    facts = ServerFacts(conn)  # type: ignore[arg-type]

    state = facts.ufw_state()
    assert state.installed is True
    assert state.active is True
