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

        ctx = ProvisionContext(ip="198.51.100.1")
        result = InstallWarp().run(conn, ctx)
        assert result.status == "ok"
        assert "already connected" in result.detail

    def test_fresh_install_returns_changed(self):
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", rc=1)
        conn.when("curl -fsSL", stdout="")
        conn.when(
            "gpg --show-keys",
            stdout="pub:-:255:22:6E2DD2174FA1C3BA:::::::\nfpr:::::::::C068A2B5771775193CBE1F2F6E2DD2174FA1C3BA:\n",
        )
        conn.when("apt-get update", stdout="")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1")
        result = InstallWarp().run(conn, ctx)
        assert result.status == "changed"
        assert str(WARP_PROXY_PORT) in result.detail

    def test_repo_codename_comes_from_os_release_not_lsb_release(self):
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", rc=1)
        conn.when("cat /etc/os-release", stdout="ID=debian\nVERSION_CODENAME=trixie\n")
        conn.when("dpkg --print-architecture", stdout="arm64")
        conn.when("curl -fsSL", stdout="")
        conn.when(
            "gpg --show-keys",
            stdout="pub:-:255:22:6E2DD2174FA1C3BA:::::::\nfpr:::::::::C068A2B5771775193CBE1F2F6E2DD2174FA1C3BA:\n",
        )
        conn.when("apt-get update", stdout="")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        result = InstallWarp().run(conn, ProvisionContext(ip="198.51.100.1"))

        assert result.status == "changed"
        repo = dict((path, text.decode()) for path, text, _ in conn._writes)[
            "/etc/apt/sources.list.d/cloudflare-client.list"
        ]
        assert repo.startswith("deb [arch=arm64 ")
        assert repo.endswith(" https://pkg.cloudflareclient.com/ trixie main\n")
        assert not any("lsb_release" in command for command in conn._calls)

    def test_already_installed_but_disconnected(self):
        """warp-cli exists but not connected — register and connect."""
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", stdout="/usr/bin/warp-cli")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1")
        result = InstallWarp().run(conn, ctx)
        assert result.status == "changed"
        # Should NOT try to install the package
        conn.assert_not_called_with_pattern("apt-get install")

    def test_gpg_key_failure_returns_failed(self):
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", rc=1)
        conn.when("curl -fsSL", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1")
        result = InstallWarp().run(conn, ctx)
        assert result.status == "failed"
        assert "signing key" in result.detail

    def test_sets_proxy_mode(self):
        """Must use proxy mode (SOCKS5), not full tunnel (would break SSH)."""
        conn = _WarpMockConnection()
        conn.when("command -v warp-cli", stdout="/usr/bin/warp-cli")
        conn.when("systemctl enable", stdout="")
        conn.when("registration new", stdout="Success")
        conn.when("mode proxy", stdout="")
        conn.when("proxy port", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1")
        InstallWarp().run(conn, ctx)
        conn.assert_called_with_pattern("mode proxy")
