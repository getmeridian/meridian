"""Tests for semantic provisioning ensure helpers."""

from __future__ import annotations

import subprocess

import pytest

from meridian.provision import ensure
from meridian.provision.ensure import ensure_service_running, ufw_rule_present
from tests.support.mock_connection import MockConnection


def test_ensure_service_running_enables_active_but_disabled_service() -> None:
    conn = (
        MockConnection()
        .when("systemctl is-active docker", stdout="active\n")
        .when("systemctl is-enabled docker", stdout="disabled\n", rc=1)
        .when("systemctl enable docker")
    )

    result = ensure_service_running(conn, "docker")

    assert result.ok is True
    assert result.changed is True
    assert "systemctl enable docker" in conn.calls
    assert not any("systemctl start docker" in call for call in conn.calls)


def test_ensure_service_running_skips_enabled_active_service() -> None:
    conn = (
        MockConnection()
        .when("systemctl is-active docker", stdout="active\n")
        .when("systemctl is-enabled docker", stdout="enabled\n")
    )

    result = ensure_service_running(conn, "docker")

    assert result.ok is True
    assert result.changed is False
    assert not any("systemctl enable docker" in call for call in conn.calls)
    assert not any("systemctl start docker" in call for call in conn.calls)


@pytest.mark.parametrize(
    ("rule", "rendered"),
    [
        (
            "allow 443/tcp comment meridian-v4-public",
            "ufw allow 443/tcp comment 'meridian-v4-public'",
        ),
        (
            "allow from 198.51.100.15 to any port 49684 proto tcp comment meridian-v4-private",
            "ufw allow from 198.51.100.15 to any port 49684 proto tcp comment 'meridian-v4-private'",
        ),
    ],
)
def test_ufw_rule_present_normalizes_quoted_comments(rule: str, rendered: str) -> None:
    output = f"Added user rules (see 'ufw status' for running firewall):\n{rendered}\n"

    assert ufw_rule_present(output, rule)
    assert not ufw_rule_present(output, rule.replace("meridian-v4-", "other-"))


_LOCKED = (
    "E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 666 (apt-get)\n"
    "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), is another process using it?"
)


class _AptSequence(MockConnection):
    """Answers apt commands from a script, one entry per call."""

    def __init__(self, *answers: tuple[int, str]) -> None:
        super().__init__()
        self._answers = list(answers)

    def run(self, command: str, timeout: int = 30, **kwargs: object):  # type: ignore[override]
        if "apt-get" in command:
            self._calls.append(command)
            rc, stderr = self._answers.pop(0)
            return subprocess.CompletedProcess(args=[], returncode=rc, stdout="", stderr=stderr)
        return super().run(command, timeout, **kwargs)


@pytest.fixture
def clock(monkeypatch):  # type: ignore[no-untyped-def]
    """Each pause advances time by its length, so waits are counted, not slept."""
    now = [0.0]
    monkeypatch.setattr(ensure.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ensure.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_apt_waits_while_another_package_manager_holds_the_lock(clock) -> None:
    conn = _AptSequence((100, _LOCKED), (100, _LOCKED), (0, ""))

    result = ensure.run_apt(conn, "apt-get update -qq", timeout=60)

    assert result.returncode == 0
    assert len(conn.calls) == 3
    assert clock[0] == 2 * ensure.APT_LOCK_RETRY_SECONDS


def test_a_lock_that_never_clears_says_who_holds_it_and_what_to_do(clock) -> None:
    conn = _AptSequence(*[(100, _LOCKED)] * 100)

    result = ensure.ensure_packages(conn, ["curl"])

    assert result.ok is False
    assert "apt-get (process 666) kept the package lock for 5 minutes" in result.detail
    assert "run the same command again" in result.detail
    assert clock[0] >= ensure.APT_LOCK_WAIT_SECONDS


def test_other_apt_failures_return_at_once(clock) -> None:
    conn = _AptSequence((100, "E: Unable to locate package nope"))

    result = ensure.run_apt(conn, "apt-get install -y nope", timeout=60)

    assert result.returncode == 100 and len(conn.calls) == 1
    assert ensure.apt_failure(result) == "E: Unable to locate package nope"
