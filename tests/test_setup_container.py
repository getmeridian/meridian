"""Tests for container deployment, port checking, and panel API polling.

Covers:
- _deploy_node_container: dir creation, file writes, docker pull retries,
  health gate polling, UFW rule, and failure modes
- _check_ports: free ports, allowed Meridian processes, conflict handling
- _wait_for_panel_api: immediate success, retry success, timeout, exceptions
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from meridian.commands.setup import _check_ports
from meridian.panel_bootstrap import deploy_node_container as _deploy_node_container, wait_for_panel_api as _wait_for_panel_api

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IP = "198.51.100.1"
_SECRET_KEY = "test-secret-key-base64"

# Returned by config imports inside _deploy_node_container
_NODE_DIR = "/opt/remnanode"
_NODE_IMAGE = "remnawave/node:latest"
_NODE_API_PORT = 3010


def _ok_result(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Simulate a successful conn.run() result."""
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail_result(stderr: str = "error", stdout: str = "") -> SimpleNamespace:
    """Simulate a failed conn.run() result."""
    return SimpleNamespace(returncode=1, stdout=stdout, stderr=stderr)


def _conn_mock() -> MagicMock:
    """ServerConnection-like mock for _deploy_node_container tests."""
    conn = MagicMock()
    conn.user = "root"
    conn.ip = _IP
    conn.run.return_value = _ok_result()
    conn.put_text.return_value = _ok_result()
    return conn


# ---------------------------------------------------------------------------
# _deploy_node_container — happy path
# ---------------------------------------------------------------------------


class TestDeployNodeContainerHappyPath:
    """Full success path: mkdir, write files, pull, start, health OK, UFW."""

    def _make_conn(self) -> MagicMock:
        """Conn that succeeds on every call, with health check returning 'true'."""
        return _conn_mock()

    def _make_healthy_conn(self) -> MagicMock:
        """Conn where health check returns 'true' on first attempt."""
        conn = self._make_conn()

        def _run_side_effect(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _ok_result(stdout="true\n")
            return _ok_result()

        conn.run.side_effect = _run_side_effect
        return conn

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_creates_node_directory(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        first_call = conn.run.call_args_list[0]
        assert "mkdir -p" in first_call[0][0]
        assert _NODE_DIR in first_call[0][0]
        assert "chmod 700" in first_call[0][0]

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_writes_env_file(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        env_writes = [c for c in conn.put_text.call_args_list if c[0][0].endswith("/.env")]
        assert len(env_writes) == 1
        assert env_writes[0].kwargs["mode"] == "600"
        assert env_writes[0].kwargs["sensitive"] is True

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_writes_compose_file(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        compose_writes = [c for c in conn.put_text.call_args_list if c[0][0].endswith("/docker-compose.yml")]
        assert len(compose_writes) == 1
        assert compose_writes[0].kwargs["mode"] == "644"

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_pulls_docker_image(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        pull_calls = [c for c in conn.run.call_args_list if c[0][0] == "docker compose pull"]
        assert len(pull_calls) == 1
        assert pull_calls[0].kwargs["cwd"] == _NODE_DIR
        assert pull_calls[0].kwargs["retries"] == 3
        assert pull_calls[0].kwargs["retry_delay"] == 10

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_starts_container(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        commands = [c[0][0] for c in conn.run.call_args_list]
        up_cmds = [c for c in commands if "docker compose up -d" in c]
        assert len(up_cmds) == 1

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_opens_ufw_port_on_healthy_container(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        commands = [c[0][0] for c in conn.run.call_args_list]
        ufw_cmds = [c for c in commands if "ufw allow" in c and str(_NODE_API_PORT) in c]
        assert len(ufw_cmds) == 1
        assert "172.16.0.0/12" in ufw_cmds[0]

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_no_warnings_on_happy_path(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = self._make_healthy_conn()
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# _deploy_node_container — docker pull retries
# ---------------------------------------------------------------------------


class TestDeployNodeContainerDockerPullRetry:
    """Pull retry logic: 3 attempts, 10s delay between."""

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_pull_succeeds_on_second_attempt(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _ok_result(stdout="true\n")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_not_called()
        pull_call = next(c for c in conn.run.call_args_list if c[0][0] == "docker compose pull")
        assert pull_call.kwargs["retries"] == 3
        assert pull_call.kwargs["retry_delay"] == 10
        mock_time.sleep.assert_not_called()

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_pull_succeeds_on_third_attempt(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _ok_result(stdout="true\n")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_not_called()
        pull_call = next(c for c in conn.run.call_args_list if c[0][0] == "docker compose pull")
        assert pull_call.kwargs["operation_name"] == "pull remnawave node image"

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_pull_fails_all_three_attempts_warns_and_returns(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker compose pull" in cmd:
                return _fail_result()
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_called_once()
        assert "pull" in mock_warn.call_args[0][0].lower()

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_pull_failure_does_not_start_container(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker compose pull" in cmd:
                return _fail_result()
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        commands = [c[0][0] for c in conn.run.call_args_list]
        assert not any("docker compose up" in c for c in commands)


# ---------------------------------------------------------------------------
# _deploy_node_container — health gate
# ---------------------------------------------------------------------------


class TestDeployNodeContainerHealthGate:
    """Health polling: 10 attempts, 3s delay, docker inspect check."""

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_health_succeeds_on_third_attempt(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()
        health_results = iter([_fail_result(), _fail_result(), _ok_result(stdout="true\n")])

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return next(health_results)
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_not_called()
        # sleep(3) called between health check attempts
        assert call(3) in mock_time.sleep.call_args_list

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_health_timeout_warns_with_docker_logs(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _fail_result()
            if "docker logs remnawave-node" in cmd:
                return _ok_result(stdout="ERROR: could not connect\n")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_called_once()
        warning_text = mock_warn.call_args[0][0]
        assert "healthy" in warning_text.lower() or "health" in warning_text.lower()
        assert "could not connect" in warning_text

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_health_timeout_still_opens_ufw(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        """UFW rule is opened regardless of health outcome."""
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _fail_result()
            if "docker logs" in cmd:
                return _ok_result(stdout="some logs")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        commands = [c[0][0] for c in conn.run.call_args_list]
        ufw_cmds = [c for c in commands if "ufw allow" in c]
        assert len(ufw_cmds) == 1

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_health_gate_polls_up_to_ten_times(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker inspect remnawave-node" in cmd:
                return _fail_result()
            if "docker logs" in cmd:
                return _ok_result(stdout="logs here")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        commands = [c[0][0] for c in conn.run.call_args_list]
        inspect_calls = [c for c in commands if "docker inspect remnawave-node" in c]
        assert len(inspect_calls) == 10


# ---------------------------------------------------------------------------
# _deploy_node_container — failure modes
# ---------------------------------------------------------------------------


class TestDeployNodeContainerFailures:
    """Non-fatal failures: mkdir, compose up."""

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_mkdir_failure_warns_and_returns_early(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "mkdir" in cmd:
                return _fail_result(stderr="Permission denied")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_called_once()
        assert _NODE_DIR in mock_warn.call_args[0][0]
        # Only mkdir was called — no further commands
        assert conn.run.call_count == 1

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_compose_up_failure_warns_and_returns_early(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker compose up" in cmd:
                return _fail_result(stderr="Cannot start service")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        mock_warn.assert_called_once()
        assert "failed to start" in mock_warn.call_args[0][0].lower()
        # No health check or UFW after compose up failure
        commands = [c[0][0] for c in conn.run.call_args_list]
        assert not any("docker inspect" in c for c in commands)
        assert not any("ufw allow" in c for c in commands)

    @patch("meridian.panel_bootstrap.time")
    @patch("meridian.panel_bootstrap.ok")
    @patch("meridian.panel_bootstrap.info")
    @patch("meridian.panel_bootstrap.warn")
    def test_compose_up_failure_includes_stderr_in_warning(
        self, mock_warn: MagicMock, mock_info: MagicMock, mock_ok: MagicMock, mock_time: MagicMock
    ) -> None:
        conn = _conn_mock()

        def _run(cmd: str, **kwargs: object) -> SimpleNamespace:
            if "docker compose up" in cmd:
                return _fail_result(stderr="port already allocated")
            return _ok_result()

        conn.run.side_effect = _run
        _deploy_node_container(conn, _SECRET_KEY)
        assert "port already allocated" in mock_warn.call_args[0][0]


# ---------------------------------------------------------------------------
# _check_ports — happy path
# ---------------------------------------------------------------------------


class TestCheckPortsHappyPath:
    """Both ports free or held by allowed Meridian processes."""

    @patch("meridian.panel_bootstrap.warn")
    def test_ports_free(self, mock_warn: MagicMock) -> None:
        """Empty ss output means port is free."""
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout="")
        _check_ports(conn, _IP, yes=True)
        mock_warn.assert_not_called()

    @patch("meridian.panel_bootstrap.warn")
    def test_port_held_by_nginx_is_allowed(self, mock_warn: MagicMock) -> None:
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:443 *:* users:(("nginx",pid=1234,fd=6))\n')
        _check_ports(conn, _IP, yes=True)
        mock_warn.assert_not_called()

    @patch("meridian.panel_bootstrap.warn")
    def test_port_held_by_xray_is_allowed(self, mock_warn: MagicMock) -> None:
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:443 *:* users:(("xray",pid=5678,fd=7))\n')
        _check_ports(conn, _IP, yes=True)
        mock_warn.assert_not_called()

    @patch("meridian.panel_bootstrap.warn")
    def test_port_held_by_docker_proxy_is_allowed(self, mock_warn: MagicMock) -> None:
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:443 *:* users:(("docker-proxy",pid=999,fd=4))\n')
        _check_ports(conn, _IP, yes=True)
        mock_warn.assert_not_called()

    @patch("meridian.panel_bootstrap.warn")
    def test_port_held_by_remnawave_node_is_allowed(self, mock_warn: MagicMock) -> None:
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:80 *:* users:(("remnawave-node",pid=444,fd=3))\n')
        _check_ports(conn, _IP, yes=True)
        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# _check_ports — conflict
# ---------------------------------------------------------------------------


class TestCheckPortsConflict:
    """Non-Meridian process on a port causes failure or prompt."""

    @patch("meridian.commands.setup.err_console")
    @patch("meridian.commands.setup.fail")
    @patch("meridian.panel_bootstrap.warn")
    def test_non_meridian_process_in_yes_mode_calls_fail(
        self, mock_warn: MagicMock, mock_fail: MagicMock, mock_err_console: MagicMock
    ) -> None:
        """With --yes, a conflicting process triggers fail() which raises Exit."""
        mock_fail.side_effect = typer.Exit(2)
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:443 *:* users:(("apache2",pid=111,fd=5))\n')
        with pytest.raises(typer.Exit):
            _check_ports(conn, _IP, yes=True)
        mock_fail.assert_called_once()
        assert "apache2" in mock_fail.call_args[0][0]

    @patch("meridian.commands.setup.err_console")
    @patch("meridian.commands.setup.choose", return_value=2)
    @patch("meridian.commands.setup.fail")
    @patch("meridian.panel_bootstrap.warn")
    def test_non_meridian_process_interactive_abort(
        self,
        mock_warn: MagicMock,
        mock_fail: MagicMock,
        mock_choose: MagicMock,
        mock_err_console: MagicMock,
    ) -> None:
        """Interactive mode: user chooses 'No' (index 2) -> fail()."""
        mock_fail.side_effect = typer.Exit(2)
        conn = MagicMock()
        conn.run.return_value = _ok_result(stdout='LISTEN 0 128 *:80 *:* users:(("apache2",pid=222,fd=3))\n')
        with pytest.raises(typer.Exit):
            _check_ports(conn, _IP, yes=False)
        mock_fail.assert_called_once()

    @patch("meridian.commands.setup.err_console")
    @patch("meridian.commands.setup.choose", return_value=1)
    @patch("meridian.panel_bootstrap.warn")
    def test_non_meridian_process_interactive_retry_then_free(
        self,
        mock_warn: MagicMock,
        mock_choose: MagicMock,
        mock_err_console: MagicMock,
    ) -> None:
        """Interactive mode: user retries and port becomes free."""
        conn = MagicMock()
        # First call: port occupied; second call: port free; third+fourth: other port checks
        conn.run.side_effect = [
            _ok_result(stdout='LISTEN 0 128 *:443 *:* users:(("apache2",pid=111,fd=5))\n'),
            _ok_result(stdout=""),  # retry: now free
            _ok_result(stdout=""),  # port 80 free
        ]
        _check_ports(conn, _IP, yes=False)
        mock_choose.assert_called_once()

    @patch("meridian.commands.setup.err_console")
    @patch("meridian.commands.setup.fail")
    @patch("meridian.panel_bootstrap.warn")
    def test_unknown_process_treated_as_conflict(
        self, mock_warn: MagicMock, mock_fail: MagicMock, mock_err_console: MagicMock
    ) -> None:
        """When regex can't extract process name, 'unknown' is not in allowed set."""
        mock_fail.side_effect = typer.Exit(2)
        conn = MagicMock()
        # Malformed users field — regex won't match
        conn.run.return_value = _ok_result(stdout="LISTEN 0 128 *:443 *:* users:((broken-format))\n")
        with pytest.raises(typer.Exit):
            _check_ports(conn, _IP, yes=True)
        mock_fail.assert_called_once()


# ---------------------------------------------------------------------------
# _wait_for_panel_api
# ---------------------------------------------------------------------------


class TestWaitForPanelApi:
    """Polling httpx.get for panel readiness."""

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_immediate_success(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """Status < 500 on first try returns True immediately."""
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp
        assert _wait_for_panel_api("https://198.51.100.2") is True
        mock_get.assert_called_once()
        mock_time.sleep.assert_not_called()

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_status_405_is_success(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """405 Method Not Allowed still means the API is up."""
        resp = MagicMock()
        resp.status_code = 405
        mock_get.return_value = resp
        assert _wait_for_panel_api("https://198.51.100.2") is True

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_status_500_retries(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """500 is not considered success — should retry."""
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_200 = MagicMock()
        resp_200.status_code = 200
        mock_get.side_effect = [resp_500, resp_200]
        assert _wait_for_panel_api("https://198.51.100.2", retries=3) is True
        assert mock_get.call_count == 2

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_all_retries_exhausted_returns_false(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """After all retries with 500s, returns False."""
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp
        assert _wait_for_panel_api("https://198.51.100.2", retries=3) is False
        assert mock_get.call_count == 3

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_connect_error_retries(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """ConnectError is caught and retried."""
        import httpx

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        mock_get.side_effect = [httpx.ConnectError("refused"), resp_ok]
        assert _wait_for_panel_api("https://198.51.100.2", retries=5) is True
        assert mock_get.call_count == 2

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_timeout_exception_retries(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """TimeoutException is caught and retried."""
        import httpx

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        mock_get.side_effect = [httpx.TimeoutException("timed out"), resp_ok]
        assert _wait_for_panel_api("https://198.51.100.2", retries=5) is True
        assert mock_get.call_count == 2

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_sleeps_between_retries_with_correct_delay(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """Verify sleep(delay) is called between retries, not after last."""
        import httpx

        mock_get.side_effect = [
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
        ]
        result = _wait_for_panel_api("https://198.51.100.2", retries=3, delay=5.0)
        assert result is False
        # Sleep between retries but not after the last attempt
        assert mock_time.sleep.call_count == 2
        mock_time.sleep.assert_called_with(5.0)

    @patch("meridian.panel_bootstrap.time")
    @patch("httpx.get")
    def test_passes_verify_false_and_timeout(self, mock_get: MagicMock, mock_time: MagicMock) -> None:
        """Ensure httpx.get is called with verify=False and timeout=10."""
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp
        _wait_for_panel_api("https://198.51.100.3")
        mock_get.assert_called_once_with(
            "https://198.51.100.3/api/auth/login",
            timeout=10,
            verify=False,
        )


# ---------------------------------------------------------------------------
# Node compose rendering
# ---------------------------------------------------------------------------


class TestRenderNodeCompose:
    """Regression tests for the generated node docker-compose.yml content.

    The fields tested here are mandated by upstream Remnawave (panel 2.6.2+,
    2.7.0+). Losing them would silently disable panel features without any
    CLI-visible error.
    """

    def test_contains_net_admin_capability(self) -> None:
        """cap_add: NET_ADMIN is MANDATORY per upstream Remnawave panel 2.6.2+
        docs. It enables the node plugin system (Torrent Blocker, Ingress /
        Egress Filter, Connection Drop) and the IP Control panel feature — all
        of which push nftables rules into the host network namespace. Without
        NET_ADMIN those syscalls fail with EPERM and the panel UI silently
        reports nothing.
        """
        from meridian.provision.remnawave_node import _render_node_compose

        content = _render_node_compose(image="remnawave/node:2.7.0", node_api_port=3010)

        assert "cap_add:" in content, "cap_add key missing — node will not accept elevated capabilities"
        assert "NET_ADMIN" in content, "NET_ADMIN capability missing — panel plugins + IP Control will silently fail"

    def test_preserves_host_networking(self) -> None:
        """`network_mode: host` is required so Xray can bind to arbitrary
        ports on the server (Reality, XHTTP, WSS). Losing it regresses core
        proxying."""
        from meridian.provision.remnawave_node import _render_node_compose

        content = _render_node_compose(image="remnawave/node:2.7.0", node_api_port=3010)
        assert "network_mode: host" in content

    def test_interpolates_image_tag(self) -> None:
        from meridian.provision.remnawave_node import _render_node_compose

        content = _render_node_compose(image="remnawave/node:2.7.0", node_api_port=3010)
        assert "image: remnawave/node:2.7.0" in content
