"""Tests for Cloudflare WARP provisioning steps."""

from __future__ import annotations

import subprocess

from meridian.provision.steps import ProvisionContext
from meridian.provision.warp import WARP_PROXY_PORT, InstallWarp
from tests.support.mock_connection import MockConnection


class _WarpMockConnection(MockConnection):
    """MockConnection that tracks warp-cli connect state.

    Returns Disconnected on first status check, Connected after connect.
    """

    def __init__(self) -> None:
        super().__init__()
        self._connected = False

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "status" in command and "warp-cli" in command:
            self._calls.append(command)
            if self._connected:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="Status: Connected", stderr="")
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="Status: Disconnected", stderr="")
        if "connect" in command and "warp-cli" in command and "disconnect" not in command:
            self._connected = True
        return super().run(command, timeout, **kwargs)


# ---------------------------------------------------------------------------
# InstallWarp
# ---------------------------------------------------------------------------


class TestInstallWarp:
    def test_already_connected_returns_ok(self):
        conn = MockConnection()
        # Pattern must match "warp-cli --accept-tos status 2>/dev/null"
        conn.when("accept-tos status", stdout="Status: Connected")

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        result = InstallWarp().run(conn, ctx)
        assert result.status == "ok"
        assert "already connected" in result.detail

    def test_fresh_install_returns_changed(self):
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", rc=1)
        conn.when("curl -fsSL", stdout="")
        conn.when("tee", stdout="")
        conn.when("apt-get update", stdout="")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        result = InstallWarp().run(conn, ctx)
        assert result.status == "changed"
        assert str(WARP_PROXY_PORT) in result.detail

    def test_already_installed_but_disconnected(self):
        """warp-cli exists but not connected — register and connect."""
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", stdout="/usr/bin/warp-cli")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        result = InstallWarp().run(conn, ctx)
        assert result.status == "changed"
        # Should NOT try to install the package
        conn.assert_not_called_with_pattern("apt-get install")

    def test_gpg_key_failure_returns_failed(self):
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", rc=1)
        conn.when("curl -fsSL", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        result = InstallWarp().run(conn, ctx)
        assert result.status == "failed"
        assert "GPG" in result.detail

    def test_sets_proxy_mode(self):
        """Must use proxy mode (SOCKS5), not full tunnel (would break SSH)."""
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", stdout="/usr/bin/warp-cli")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        InstallWarp().run(conn, ctx)
        conn.assert_called_with_pattern("mode proxy")


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


class TestWarpPipelineIntegration:
    def test_warp_disabled_no_steps(self):
        from meridian.provision import build_setup_steps

        ctx = ProvisionContext(ip="198.51.100.1", warp=False)
        steps = build_setup_steps(ctx)
        step_names = [s.name for s in steps]
        assert "Install Cloudflare WARP" not in step_names

    def test_warp_enabled_adds_install_step(self):
        from meridian.provision import build_setup_steps

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        steps = build_setup_steps(ctx)
        step_names = [s.name for s in steps]
        assert "Install Cloudflare WARP" in step_names

    def test_warp_steps_before_verify(self):
        """WARP steps must come after Docker."""
        from meridian.provision import build_setup_steps

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        steps = build_setup_steps(ctx)
        step_names = [s.name for s in steps]
        warp_idx = step_names.index("Install Cloudflare WARP")
        docker_idx = step_names.index("Install Docker")
        assert warp_idx > docker_idx

    def test_warp_steps_after_node_deploy(self):
        """WARP steps must come after Docker install."""
        from meridian.provision import build_setup_steps

        ctx = ProvisionContext(ip="198.51.100.1", warp=True)
        steps = build_setup_steps(ctx)
        step_names = [s.name for s in steps]
        docker_idx = step_names.index("Install Docker")
        warp_idx = step_names.index("Install Cloudflare WARP")
        assert warp_idx > docker_idx

    def test_node_recipe_honors_per_exit_warp_choice(self):
        from meridian.provision import build_node_steps

        direct_steps = build_node_steps(ProvisionContext(ip="198.51.100.20", is_panel_host=False, warp=False))
        warp_steps = build_node_steps(ProvisionContext(ip="198.51.100.30", is_panel_host=False, warp=True))

        assert "Install Cloudflare WARP" not in [step.name for step in direct_steps]
        warp_names = [step.name for step in warp_steps]
        assert "Install Cloudflare WARP" in warp_names
        assert warp_names.index("Install Cloudflare WARP") > warp_names.index("Install Docker")
