"""Tests for setup wizard — detect_public_ip and run() entry points."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import typer

from meridian.commands.resolve import detect_public_ip
from meridian.commands.setup import (
    _build_redeploy_command,
    _print_success,
    _regenerate_connection_pages_after_deploy,
    run,
)
from meridian.config import is_ipv4
from meridian.credentials import ClientEntry, ServerCredentials


class TestDetectPublicIP:
    def test_returns_valid_ip(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="93.184.216.34\n", stderr="")
        with patch("meridian.commands.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == "93.184.216.34"
        assert is_ipv4(ip)

    def test_returns_empty_on_timeout(self) -> None:
        with patch(
            "meridian.commands.resolve.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=5),
        ):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_not_found(self) -> None:
        with patch(
            "meridian.commands.resolve.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_invalid_output(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="not-an-ip\n", stderr="")
        with patch("meridian.commands.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == ""

    def test_returns_empty_on_curl_failure(self) -> None:
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch("meridian.commands.resolve.subprocess.run", return_value=mock_result):
            ip = detect_public_ip()
        assert ip == ""

    def test_tries_fallback_url(self) -> None:
        """If first URL fails, should try the fallback."""
        calls = []

        def side_effect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            calls.append(args)
            # First call fails, second succeeds
            if len(calls) == 1:
                return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="10.0.0.1\n", stderr="")

        with patch("meridian.commands.resolve.subprocess.run", side_effect=side_effect):
            ip = detect_public_ip()
        assert ip == "10.0.0.1"
        assert len(calls) == 2


class TestRunWithExplicitIP:
    """Test run() behavior when IP is provided explicitly (non-interactive)."""

    def test_invalid_ip_exits(self) -> None:
        """run() with invalid IP should fail."""
        with pytest.raises(typer.Exit):
            run(ip="not-an-ip", yes=True)

    def test_both_ip_and_server_flag_fails(self, tmp_home: Path) -> None:
        """Cannot use both positional IP and --server flag."""
        with pytest.raises(typer.Exit):
            run(ip="1.2.3.4", requested_server="mybox", yes=True)

    def test_server_flag_resolves_ip_from_registry(self, servers_file: Path, tmp_home: Path) -> None:
        """--server flag with a known name should resolve to its IP."""
        from meridian.servers import ServerEntry, ServerRegistry

        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("10.20.30.40", "root", "prod"))

        # Verify the registry lookup works (this is what run() does internally)
        entry = reg.find("prod")
        assert entry is not None
        assert entry.host == "10.20.30.40"
        assert entry.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path, tmp_home: Path) -> None:
        """--server with unknown name and non-IP string should fail."""
        with pytest.raises(typer.Exit):
            run(requested_server="nonexistent", yes=True)

    def test_force_refreshes_credentials_before_deploy(self, tmp_home: Path) -> None:
        """Deploy must refresh from the server before trusting cached local creds."""
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=object(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=True) as mock_fetch,
            patch("meridian.commands.setup._run_provisioner"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_fetch.assert_called_once_with(resolved, force=True)

    def test_redeploy_keeps_the_saved_camouflage_target(self, tmp_home: Path, monkeypatch) -> None:
        """Changing DEFAULT_SNI must not silently move existing servers (their links would break)."""
        import meridian.commands.setup as setup_mod

        monkeypatch.setattr(setup_mod, "creds_dir_for", lambda ip, local_mode: tmp_home / "credentials" / ip)
        resolved = SimpleNamespace(
            ip="1.2.3.4", user="root", conn=Mock(), creds_dir=tmp_home / "credentials" / "1.2.3.4"
        )
        resolved.creds_dir.mkdir(parents=True)
        creds = ServerCredentials()
        creds.server.sni = "www.microsoft.com"
        creds.save(resolved.creds_dir / "proxy.yml")

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=True),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        assert mock_provision.call_args.args[2] == "www.microsoft.com"

    def test_deploy_proceeds_when_refresh_fails_with_cache_but_no_remote_state(self, tmp_home: Path) -> None:
        """Redeploy should proceed with local cache when server has no credentials (pre-sync deploy)."""
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=Mock(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        creds = ServerCredentials()
        creds.panel.username = "admin"
        creds.panel.password = "secret"
        creds.save(resolved.creds_dir / "proxy.yml")

        # Server has no /etc/meridian/proxy.yml (rc=1 from test -s)
        resolved.conn.run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
        )

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=False),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_provision.assert_called_once()

    def test_deploy_fails_when_refresh_fails_with_cache_and_remote_state_exists(self, tmp_home: Path) -> None:
        """Redeploy must fail when server has credentials but SCP can't fetch them."""
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=Mock(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        creds = ServerCredentials()
        creds.panel.username = "admin"
        creds.panel.password = "secret"
        creds.save(resolved.creds_dir / "proxy.yml")

        # Server HAS /etc/meridian/proxy.yml (rc=0 from test -s)
        resolved.conn.run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=False),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
        ):
            with pytest.raises(typer.Exit):
                run(ip="1.2.3.4", yes=True)

        mock_provision.assert_not_called()

    def test_deploy_allows_refresh_failure_on_fresh_server(self, tmp_home: Path) -> None:
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=Mock(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        resolved.conn.run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
        )

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=False),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_provision.assert_called_once()

    def test_deploy_fails_when_refresh_fails_without_cache_but_remote_state_exists(self, tmp_home: Path) -> None:
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=Mock(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        resolved.conn.run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=False),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
        ):
            with pytest.raises(typer.Exit):
                run(ip="1.2.3.4", yes=True)

        mock_provision.assert_not_called()

    def test_deploy_proceeds_when_remote_state_check_is_inconclusive_and_no_cache(self, tmp_home: Path) -> None:
        """Fresh deploy should proceed even when remote state check is inconclusive."""
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=Mock(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        resolved.conn.run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=124,
            stdout="",
            stderr="timed out",
        )

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=False),
            patch("meridian.commands.setup._run_provisioner") as mock_provision,
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_provision.assert_called_once()

    def test_regenerates_pages_after_deploy(self, tmp_home: Path) -> None:
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=object(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        creds = ServerCredentials()
        creds.server.ip = "1.2.3.4"
        creds.clients = [ClientEntry(name="default", reality_uuid="r-uuid", wss_uuid="w-uuid")]
        creds.save(resolved.creds_dir / "proxy.yml")

        with (
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=True),
            patch("meridian.commands.setup._run_provisioner"),
            patch("meridian.commands.setup._sync_credentials_to_server", return_value=True),
            patch("meridian.commands.setup._regenerate_connection_pages_after_deploy") as mock_regen,
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_regen.assert_called_once_with(resolved)

    def test_regeneration_failure_warns_and_deploy_still_succeeds(self, tmp_home: Path) -> None:
        resolved = SimpleNamespace(
            ip="1.2.3.4",
            user="root",
            conn=object(),
            creds_dir=tmp_home / "credentials" / "1.2.3.4",
        )
        resolved.creds_dir.mkdir(parents=True)
        mock_registry = Mock()

        with (
            patch("meridian.commands.setup.ServerRegistry", return_value=mock_registry),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.fetch_credentials", return_value=True),
            patch("meridian.commands.setup._run_provisioner"),
            patch("meridian.commands.setup._sync_credentials_to_server", return_value=True),
            patch(
                "meridian.commands.setup._regenerate_connection_pages_after_deploy",
                side_effect=RuntimeError("boom"),
            ),
            patch("meridian.commands.setup.warn") as mock_warn,
            patch("meridian.commands.setup._print_success") as mock_success,
            patch("meridian.commands.setup._offer_relay"),
        ):
            run(ip="1.2.3.4", yes=True)

        mock_registry.add.assert_called_once()
        mock_success.assert_called_once()
        mock_warn.assert_any_call("Could not refresh connection pages after deploy: boom")


class TestSuccessOutput:
    def _write_proxy(self, creds_dir: Path, *, domain: str = "", geo_block: bool = False) -> None:
        creds = ServerCredentials()
        creds.server.ip = "1.2.3.4"
        creds.server.domain = domain or None
        creds.server.hosted_page = True
        creds.server.geo_block = geo_block
        creds.panel.info_page_path = "connect"
        creds.panel.url = f"https://{domain or '1.2.3.4'}/panel/"
        creds.panel.username = "admin"
        creds.panel.password = "secret"
        creds.reality.uuid = "r-uuid"
        creds.save(creds_dir / "proxy.yml")

    def test_domain_success_includes_cloudflare_steps(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds_dir = tmp_home / "credentials" / "1.2.3.4"
        creds_dir.mkdir(parents=True)
        self._write_proxy(creds_dir, domain="example.com")
        resolved = SimpleNamespace(ip="1.2.3.4", creds_dir=creds_dir)

        _print_success(resolved, "default", "example.com", redeploy_cmd="meridian deploy 1.2.3.4 --yes")

        out = capsys.readouterr().err
        assert "Cloudflare setup" in out
        assert "DNS only" in out
        assert "Full (Strict)" in out
        assert "Website Analytics / RUM" in out
        assert "getmeridian.org/ping" not in out

    def test_ip_mode_success_omits_external_ping_hint(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds_dir = tmp_home / "credentials" / "1.2.3.4"
        creds_dir.mkdir(parents=True)
        self._write_proxy(creds_dir)
        resolved = SimpleNamespace(ip="1.2.3.4", creds_dir=creds_dir)

        _print_success(resolved, "default", "", redeploy_cmd="meridian deploy 1.2.3.4 --yes")

        out = capsys.readouterr().err
        assert "getmeridian.org/ping" not in out
        assert "Cloudflare setup" not in out

    def test_success_output_mentions_enabled_geo_blocking(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        creds_dir = tmp_home / "credentials" / "1.2.3.4"
        creds_dir.mkdir(parents=True)
        self._write_proxy(creds_dir, geo_block=True)
        resolved = SimpleNamespace(ip="1.2.3.4", creds_dir=creds_dir)

        _print_success(resolved, "default", "", redeploy_cmd="meridian deploy 1.2.3.4 --yes")

        out = capsys.readouterr().err
        assert "Geo-blocking is ON" in out
        assert "--no-geo-block" in out


class TestRegenerateConnectionPagesAfterDeploy:
    def test_calls_regenerator_for_saved_clients(self, tmp_home: Path) -> None:
        creds_dir = tmp_home / "credentials" / "1.2.3.4"
        creds_dir.mkdir(parents=True)
        resolved = SimpleNamespace(ip="1.2.3.4", creds_dir=creds_dir, conn=object())
        creds = ServerCredentials()
        creds.server.ip = "1.2.3.4"
        creds.clients = [ClientEntry(name="alice", reality_uuid="r-uuid", wss_uuid="w-uuid")]
        creds.save(creds_dir / "proxy.yml")

        with patch("meridian.commands.relay._regenerate_client_pages") as mock_regen:
            _regenerate_connection_pages_after_deploy(resolved)

        mock_regen.assert_called_once()


class TestIsIPv4:
    """Test the is_ipv4 helper used by setup."""

    def test_valid_ips(self) -> None:
        assert is_ipv4("1.2.3.4") is True
        assert is_ipv4("255.255.255.255") is True
        assert is_ipv4("0.0.0.0") is True
        assert is_ipv4("192.168.1.1") is True

    def test_invalid_ips(self) -> None:
        assert is_ipv4("") is False
        assert is_ipv4("256.1.1.1") is False
        assert is_ipv4("1.2.3") is False
        assert is_ipv4("1.2.3.4.5") is False
        assert is_ipv4("abc.def.ghi.jkl") is False
        assert is_ipv4("not-an-ip") is False
        assert is_ipv4("1.2.3.-1") is False


class TestBuildRedeployCommand:
    def test_minimal_defaults(self) -> None:
        resolved = SimpleNamespace(ip="1.2.3.4", user="root")
        cmd = _build_redeploy_command(
            resolved,
            sni="",
            domain="",
            client_name="default",
            harden=True,
            server_name="",
            icon="",
            color="",
            pq=False,
            warp=False,
            geo_block=True,
        )
        assert cmd == "meridian deploy 1.2.3.4 --yes"

    def test_full_options(self) -> None:
        resolved = SimpleNamespace(ip="198.51.100.1", user="armenqa")
        cmd = _build_redeploy_command(
            resolved,
            sni="cdn.example.net",
            domain="vpn.example.com",
            client_name="ARMIK",
            harden=False,
            server_name="My VPN",
            icon="🛡️",
            color="sunset",
            pq=True,
            warp=True,
            geo_block=False,
        )
        assert "198.51.100.1" in cmd
        assert "--user armenqa" in cmd
        assert "--sni cdn.example.net" in cmd
        assert "--domain vpn.example.com" in cmd
        assert "--client-name ARMIK" in cmd
        assert "--no-harden" in cmd
        assert "--pq" in cmd
        assert "--warp" in cmd
        assert "--no-geo-block" in cmd
        assert "--display-name 'My VPN'" in cmd
        assert "--color sunset" in cmd
        assert "--yes" in cmd

    def test_default_sni_omitted(self) -> None:
        from meridian.config import DEFAULT_SNI

        resolved = SimpleNamespace(ip="1.2.3.4", user="root")
        cmd = _build_redeploy_command(
            resolved,
            sni=DEFAULT_SNI,
            domain="",
            client_name="",
            harden=True,
            server_name="",
            icon="",
            color="ocean",
            pq=False,
            warp=False,
            geo_block=True,
        )
        assert "--sni" not in cmd
        assert "--color" not in cmd
