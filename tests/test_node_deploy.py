"""Focused contracts for node deployment helpers."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from meridian.node_deploy import (
    check_panel_api_ready,
    create_api_token,
    deploy_node_container,
    panel_base_url,
    render_node_compose,
    wait_for_node_connected,
)


def test_create_api_token_uses_remnawave_v28_request_schema() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"response": {"token": "provisioner-token"}}

    with patch("httpx.post", return_value=response) as post:
        token = create_api_token("https://198.51.100.10/secret/", "admin-token")

    assert token == "provisioner-token"
    post.assert_called_once_with(
        "https://198.51.100.10/secret/api/tokens",
        json={
            "name": "meridian-provisioner",
            "expiresInDays": 100_000,
            "scopes": ["*"],
        },
        headers={
            "Authorization": "Bearer admin-token",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=True,
    )


def test_panel_base_url_brackets_ipv6_literals() -> None:
    assert panel_base_url("2001:0DB8:0:0:0:0:0:11", "", "secret") == ("https://[2001:0DB8:0:0:0:0:0:11]/secret/")


@patch("meridian.health.time.sleep")
def test_wait_for_node_connected_polls_panel_state(_sleep: MagicMock) -> None:
    panel = MagicMock()
    panel.get_node.side_effect = [
        None,
        SimpleNamespace(is_connected=False),
        SimpleNamespace(is_connected=True),
    ]

    assert wait_for_node_connected(panel, "node-uuid", timeout=1, interval=0.01)
    assert panel.get_node.call_count == 3


_NODE_DIR = "/opt/netagent"


def _result(rc: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _deploy(caplog: pytest.LogCaptureFixture, respond=lambda cmd: None) -> MagicMock:
    """Run deploy_node_container; *respond* overrides results by command, else success."""
    conn = MagicMock()
    conn.user = "root"
    conn.ip = "198.51.100.1"
    conn.put_text.return_value = _result()
    conn.run.side_effect = lambda cmd, **_: respond(cmd) or _result()
    with caplog.at_level(logging.WARNING, logger="meridian.panel_bootstrap"):
        deploy_node_container(conn, "test-secret-key-base64")
    return conn


def _commands(conn: MagicMock) -> list[str]:
    return [c.args[0] for c in conn.run.call_args_list]


def _healthy(cmd: str) -> SimpleNamespace | None:
    return _result(stdout="true\n") if "docker inspect netagent" in cmd else None


@patch("meridian.health.time.sleep")
def test_node_deploy_writes_private_files_then_pulls_and_starts(sleep: MagicMock, caplog) -> None:
    conn = _deploy(caplog, _healthy)

    mkdir = _commands(conn)[0]
    assert "mkdir -p" in mkdir and _NODE_DIR in mkdir and "chmod 700" in mkdir
    writes = [(c.args[0].rsplit("/", 1)[-1], c.kwargs) for c in conn.put_text.call_args_list]
    env = [kwargs for name, kwargs in writes if name == ".env"]
    compose = [kwargs for name, kwargs in writes if name == "docker-compose.yml"]
    assert len(env) == 1 and env[0]["mode"] == "600" and env[0]["sensitive"] is True
    assert len(compose) == 1 and compose[0]["mode"] == "644"
    pulls = [c for c in conn.run.call_args_list if c.args[0] == "docker compose pull"]
    assert len(pulls) == 1
    assert pulls[0].kwargs["cwd"] == _NODE_DIR
    assert pulls[0].kwargs["retries"] == 3 and pulls[0].kwargs["retry_delay"] == 10
    assert pulls[0].kwargs["operation_name"] == "pull Remnawave node images"
    assert sum("docker compose up -d" in c for c in _commands(conn)) == 1
    assert not caplog.records
    sleep.assert_not_called()


@patch("meridian.health.time.sleep")
def test_node_api_rule_replaces_the_pre_rename_comment(_sleep: MagicMock, caplog) -> None:
    commands = _commands(_deploy(caplog, _healthy))

    ufw = [c for c in commands if "ufw allow" in c and "3010" in c]
    assert len(ufw) == 1
    assert "172.16.0.0/12" in ufw[0]
    assert "comment fw-node-api" in ufw[0] and "Meridian" not in ufw[0]
    # UFW keeps an identical rule's old comment, so the pre-rename rule is deleted first.
    legacy = next(i for i, c in enumerate(commands) if "# Meridian node API (Docker internal)" in c)
    assert legacy < commands.index(ufw[0])


@pytest.mark.parametrize(
    ("failing", "stderr", "warning", "never_runs"),
    [
        ("mkdir", "Permission denied", _NODE_DIR, "docker compose"),
        ("docker compose pull", "error", "pull", "docker compose up"),
        (
            "docker compose up",
            "port already allocated",
            "compose up failed: port already allocated",
            "docker inspect netagent",
        ),
    ],
)
@patch("meridian.health.time.sleep")
def test_node_deploy_step_failure_warns_once_and_stops(_sleep, caplog, failing, stderr, warning, never_runs) -> None:
    conn = _deploy(caplog, lambda cmd: _result(1, stderr=stderr) if failing in cmd else None)

    assert len(caplog.records) == 1
    assert warning in caplog.records[0].message.lower()
    commands = _commands(conn)
    assert not any(never_runs in c for c in commands)
    assert not any("ufw allow" in c for c in commands)
    if failing == "mkdir":
        assert len(commands) == 1


def _advancing_clock(mono: MagicMock, step: float = 3.0) -> None:
    ticks = iter(range(10_000))
    mono.side_effect = lambda: next(ticks) * step


@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic")
def test_node_health_gate_retries_until_healthy(mono: MagicMock, sleep: MagicMock, caplog) -> None:
    _advancing_clock(mono)
    health = iter([_result(1), _result(1), _result(stdout="true\n")])

    _deploy(caplog, lambda cmd: next(health) if "docker inspect netagent" in cmd else None)

    assert not caplog.records
    assert sleep.call_count >= 2


@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic")
def test_unhealthy_node_warns_after_polling_but_still_opens_the_node_api(mono, _sleep, caplog) -> None:
    _advancing_clock(mono)

    def respond(cmd: str) -> SimpleNamespace | None:
        if "docker inspect netagent" in cmd:
            return _result(1)
        if "docker logs netagent" in cmd:
            return _result(stdout="ERROR: could not connect\n")
        return None

    commands = _commands(_deploy(caplog, respond))

    assert len(caplog.records) == 1
    assert "health" in caplog.records[0].message.lower()
    # A 30s timeout at a 3s interval polls about ten times.
    assert sum("docker inspect netagent" in c for c in commands) >= 5
    assert sum("ufw allow" in c for c in commands) == 1


def _status(code: int) -> MagicMock:
    return MagicMock(status_code=code)


@pytest.mark.parametrize("code", [200, 405])
@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic", return_value=0.0)
@patch("httpx.get")
def test_panel_api_ready_on_any_non_5xx_answer_over_verified_tls(get, _mono, sleep, code) -> None:
    get.return_value = _status(code)

    assert check_panel_api_ready("https://198.51.100.3") is True
    # Readiness never accepts the self-signed bootstrap certificate.
    get.assert_called_once_with("https://198.51.100.3/api/auth/login", timeout=10, verify=True)
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "first",
    [_status(500), httpx.ConnectError("refused"), httpx.TimeoutException("timed out")],
    ids=["status-500", "connect-error", "timeout"],
)
@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic", side_effect=[0.0, 0.0, 3.0, 3.0])
@patch("httpx.get")
def test_panel_api_ready_retries_server_errors_and_transport_failures(get, _mono, _sleep, first) -> None:
    get.side_effect = [first, _status(200)]

    assert check_panel_api_ready("https://198.51.100.2", retries=3) is True
    assert get.call_count == 2


@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic", side_effect=[0.0, 0.0, 4.5, 9.0])
@patch("httpx.get", return_value=_status(500))
def test_panel_api_not_ready_after_retries_are_exhausted(get, _mono, _sleep) -> None:
    # retries=3 at the default 3s delay is a 9s budget.
    assert check_panel_api_ready("https://198.51.100.2", retries=3) is False
    assert get.call_count == 3


@patch("meridian.health.time.sleep")
@patch("meridian.health.time.monotonic", side_effect=[0.0, 0.0, 5.0, 5.0, 10.0, 10.0, 15.0])
@patch("httpx.get", side_effect=httpx.ConnectError("refused"))
def test_panel_api_readiness_sleeps_between_attempts(_get, _mono, sleep) -> None:
    assert check_panel_api_ready("https://198.51.100.2", retries=3, delay=5.0) is False
    assert sleep.call_count >= 2


def test_node_compose_keeps_upstream_mandated_fields() -> None:
    """Remnawave 2.6.2+ node plugins and IP Control push nftables rules and need NET_ADMIN
    (without it they fail with a swallowed EPERM); Xray binds arbitrary ports, so host networking."""
    content = render_node_compose(image="remnawave/node:2.7.0")

    assert "cap_add:" in content and "NET_ADMIN" in content
    assert "network_mode: host" in content
    assert "image: remnawave/node:2.7.0" in content
