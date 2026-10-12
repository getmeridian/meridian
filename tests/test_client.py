"""Tests for client add/list/remove commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.client import run_add, run_list, run_remove, run_show
from meridian.panel import Inbound, PanelError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inbound(
    id: int = 1,
    remark: str = "VLESS-Reality",
    port: int = 443,
    clients: list[dict] | None = None,
) -> Inbound:
    return Inbound(
        id=id,
        remark=remark,
        protocol="vless",
        port=port,
        clients=clients or [],
    )


def _make_reality_inbound(name: str = "default", port: int = 443) -> Inbound:
    return _make_inbound(
        id=1,
        remark="VLESS-Reality",
        port=port,
        clients=[{"id": "existing-uuid", "email": f"reality-{name}", "flow": "xtls-rprx-vision"}],
    )


def _make_xhttp_inbound(name: str = "default", port: int = 34567) -> Inbound:
    return _make_inbound(
        id=2,
        remark="VLESS-Reality-XHTTP",
        port=port,
        clients=[{"id": "existing-uuid", "email": f"xhttp-{name}", "flow": ""}],
    )


def _make_mock_resolved(creds_dir: Path) -> MagicMock:
    """Return a mock ResolvedServer."""
    resolved = MagicMock()
    resolved.ip = "1.2.3.4"
    resolved.user = "root"
    resolved.local_mode = False
    resolved.creds_dir = creds_dir
    # Make conn.run() succeed by default (for credential sync)
    resolved.conn.run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    return resolved


def _make_mock_panel(inbounds: list[Inbound], uuid_seq: list[str] | None = None) -> MagicMock:
    """Return a mock PanelClient."""
    panel = MagicMock()
    panel.list_inbounds.return_value = inbounds

    if uuid_seq:
        panel.generate_uuid.side_effect = uuid_seq
    else:
        panel.generate_uuid.return_value = "new-test-uuid-1234"

    panel.add_client.return_value = None
    panel.remove_client.return_value = None
    return panel


def _write_proxy_yml(creds_dir: Path, *, domain: str = "", extra_client: str = "", hosted_page: bool = False) -> None:
    """Write a minimal v2 proxy.yml to creds_dir."""
    clients_section = ""
    if extra_client:
        clients_section = f"""\
clients:
  - name: {extra_client}
    added: "2026-01-01T00:00:00Z"
    reality_uuid: existing-uuid
    wss_uuid: ""
"""
    else:
        clients_section = "clients: []\n"

    domain_line = f"  domain: {domain}\n" if domain else ""
    content = f"""\
version: 2
panel:
  username: admin
  password: secret
  web_base_path: abc123
  port: 2053
server:
  ip: 1.2.3.4
  sni: www.cloudflare.com
  hosted_page: {"true" if hosted_page else "false"}
{domain_line}protocols:
  reality:
    uuid: existing-uuid
    public_key: testpubkey
    short_id: abcd1234
{clients_section}"""
    proxy = creds_dir / "proxy.yml"
    proxy.write_text(content)
    proxy.chmod(0o600)


# ---------------------------------------------------------------------------
# run_add tests
# ---------------------------------------------------------------------------


class TestRunAdd:
    def test_add_client_success(self, tmp_home: Path, creds_dir: Path) -> None:
        """Happy path: add client with Reality + XHTTP inbounds (no domain)."""
        _write_proxy_yml(creds_dir)

        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
            _make_inbound(id=2, remark="VLESS-Reality-XHTTP", port=34567, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds, uuid_seq=["new-uuid-1"])
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
            patch("meridian.commands.client.print_terminal_output"),
            patch("meridian.commands.client.save_connection_html"),
        ):
            run_add("alice")

        # Panel add_client should be called for each active inbound
        assert mock_panel.add_client.call_count >= 1
        # UUID was generated
        assert mock_panel.generate_uuid.called
        # Credentials were saved with new client
        proxy_file = creds_dir / "proxy.yml"
        assert proxy_file.exists()
        content = proxy_file.read_text()
        assert "alice" in content

    def test_add_duplicate_client_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client already tracked in credentials — should fail."""
        _write_proxy_yml(creds_dir, extra_client="alice")

        inbounds = [_make_reality_inbound("alice")]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_add("alice")
        assert exc.value.exit_code == 1

    def test_add_client_panel_duplicate_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client email already exists in panel — should fail."""
        _write_proxy_yml(creds_dir)

        # Panel has alice in the reality inbound but creds don't
        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[{"id": "old-uuid", "email": "reality-alice", "flow": "xtls-rprx-vision"}],
            )
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_add("alice")
        assert exc.value.exit_code == 1
        # Panel add_client should NOT have been called
        mock_panel.add_client.assert_not_called()

    def test_add_client_no_reality_inbound_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """No Reality inbound on the server — should fail."""
        _write_proxy_yml(creds_dir)

        # Only XHTTP inbound, no Reality
        inbounds = [_make_inbound(id=2, remark="VLESS-Reality-XHTTP", port=34567, clients=[])]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_add("bob")
        assert exc.value.exit_code == 1

    def test_add_client_with_domain_adds_wss(self, tmp_home: Path, creds_dir: Path) -> None:
        """When domain is set and WSS inbound exists, client is added to WSS too."""
        _write_proxy_yml(creds_dir, domain="example.com")

        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
            _make_inbound(id=3, remark="VLESS-WSS", port=8443, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds, uuid_seq=["uuid-reality", "uuid-wss"])
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
            patch("meridian.commands.client.print_terminal_output"),
            patch("meridian.commands.client.save_connection_html"),
        ):
            run_add("carol")

        # Should be called twice: reality + wss
        assert mock_panel.add_client.call_count == 2


# ---------------------------------------------------------------------------
# run_show tests
# ---------------------------------------------------------------------------


class TestRunShow:
    def test_show_client_success(self, tmp_home: Path, creds_dir: Path) -> None:
        """Happy path: show connection info for an existing client."""
        _write_proxy_yml(creds_dir, extra_client="alice")

        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client.print_terminal_output") as mock_print,
        ):
            run_show("alice")

        # print_terminal_output should have been called with header_verb="connection info"
        mock_print.assert_called_once()
        call_kwargs = mock_print.call_args
        assert call_kwargs.kwargs.get("header_verb") == "connection info"

    def test_show_nonexistent_client_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client not in credentials and not in panel -- should fail."""
        _write_proxy_yml(creds_dir)

        mock_resolved = _make_mock_resolved(creds_dir)

        # Panel has a reality inbound but no matching client
        inbounds = [_make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[])]
        mock_panel = _make_mock_panel(inbounds)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_show("ghost")
        assert exc.value.exit_code == 1

    def test_show_recovers_from_panel(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client missing from credentials but found in panel -- should recover and show."""
        _write_proxy_yml(creds_dir)

        mock_resolved = _make_mock_resolved(creds_dir)

        # Panel has alice in reality inbound but local creds do not
        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[{"id": "recovered-uuid", "email": "reality-alice", "flow": "xtls-rprx-vision"}],
            ),
        ]
        mock_panel = _make_mock_panel(inbounds)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
            patch("meridian.commands.client.print_terminal_output") as mock_print,
        ):
            run_show("alice")

        # Should have printed connection info
        mock_print.assert_called_once()
        assert mock_print.call_args.kwargs.get("header_verb") == "connection info"

        # Credentials should have been synced back
        proxy_content = (creds_dir / "proxy.yml").read_text()
        assert "alice" in proxy_content
        assert "recovered-uuid" in proxy_content

    def test_show_panel_error_falls_back_to_fail(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client not in credentials and panel errors -- should fail gracefully."""
        _write_proxy_yml(creds_dir)

        mock_resolved = _make_mock_resolved(creds_dir)

        mock_panel = MagicMock()
        mock_panel.list_inbounds.side_effect = PanelError("unauthorized")

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_show("ghost")
        assert exc.value.exit_code == 1

    def test_show_invalid_name_fails(self, tmp_home: Path) -> None:
        """Invalid name should fail at validation."""
        with pytest.raises(typer.Exit) as exc:
            run_show("bad name!")
        assert exc.value.exit_code == 1


# ---------------------------------------------------------------------------
# run_remove tests
# ---------------------------------------------------------------------------


class TestRunRemove:
    def test_remove_client_success(self, tmp_home: Path, creds_dir: Path) -> None:
        """Happy path: remove client from all inbounds."""
        _write_proxy_yml(creds_dir, extra_client="alice")

        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[{"id": "alice-uuid", "email": "reality-alice", "flow": "xtls-rprx-vision"}],
            ),
            _make_inbound(
                id=2,
                remark="VLESS-Reality-XHTTP",
                port=34567,
                clients=[{"id": "alice-uuid", "email": "xhttp-alice", "flow": ""}],
            ),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
        ):
            run_remove("alice")

        # remove_client called for each inbound where alice appears
        assert mock_panel.remove_client.call_count >= 1
        # Client removed from credentials
        proxy_file = creds_dir / "proxy.yml"
        content = proxy_file.read_text()
        assert "alice" not in content

    def test_remove_nonexistent_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """Client not found in panel — should fail."""
        _write_proxy_yml(creds_dir)

        # Reality inbound has no client named "ghost"
        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_remove("ghost")
        assert exc.value.exit_code == 1
        mock_panel.remove_client.assert_not_called()

    def test_remove_stale_local_client_reconciles_when_panel_already_missing(
        self, tmp_home: Path, creds_dir: Path
    ) -> None:
        """A stale local client entry should be removable even if the panel no longer has it."""
        _write_proxy_yml(creds_dir, extra_client="alice")

        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
        ):
            run_remove("alice")

        assert "alice" not in (creds_dir / "proxy.yml").read_text()

    def test_remove_sync_failure_restores_local_credentials_and_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        _write_proxy_yml(creds_dir, extra_client="alice")
        original = (creds_dir / "proxy.yml").read_text()

        inbounds = [
            _make_reality_inbound("alice"),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_remove("alice")
        assert exc.value.exit_code == 1
        assert (creds_dir / "proxy.yml").read_text() == original

    def test_remove_hosted_page_uses_uuid_before_forgetting_client(self, tmp_home: Path, creds_dir: Path) -> None:
        _write_proxy_yml(creds_dir, extra_client="alice", hosted_page=True)

        inbounds = [
            _make_reality_inbound("alice"),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
        ):
            run_remove("alice")

        mock_resolved.conn.run.assert_any_call("rm -rf /var/www/private/existing-uuid", timeout=10)

    def test_remove_no_reality_inbound_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """No Reality inbound on the server — should fail."""
        _write_proxy_yml(creds_dir)

        inbounds: list[Inbound] = []
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_remove("alice")
        assert exc.value.exit_code == 1


# ---------------------------------------------------------------------------
# run_list tests
# ---------------------------------------------------------------------------


class TestRunList:
    def test_list_clients(self, tmp_home: Path, creds_dir: Path) -> None:
        """List command shows clients from reality inbound."""
        _write_proxy_yml(creds_dir, extra_client="default")

        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[
                    {"id": "uuid-alice", "email": "reality-alice", "flow": "xtls-rprx-vision", "enable": True},
                    {"id": "uuid-bob", "email": "reality-bob", "flow": "xtls-rprx-vision", "enable": False},
                ],
            ),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            # Should not raise
            run_list()

        mock_panel.list_inbounds.assert_called_once()

    def test_list_empty(self, tmp_home: Path, creds_dir: Path) -> None:
        """List with no clients — should not crash."""
        _write_proxy_yml(creds_dir)

        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            run_list()  # Should not raise

    def test_list_panel_error_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        """Panel error during list — should fail with exit code 1."""
        _write_proxy_yml(creds_dir)

        mock_panel = MagicMock()
        mock_panel.list_inbounds.side_effect = PanelError("unauthorized")
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_list()
        assert exc.value.exit_code == 1


# ---------------------------------------------------------------------------
# Client name validation tests
# ---------------------------------------------------------------------------


class TestValidateClientName:
    def test_invalid_name_empty_fails(self, tmp_home: Path) -> None:
        with pytest.raises(typer.Exit) as exc:
            run_add("")
        assert exc.value.exit_code == 1

    def test_invalid_name_space_fails(self, tmp_home: Path) -> None:
        with pytest.raises(typer.Exit) as exc:
            run_add("has space")
        assert exc.value.exit_code == 1

    def test_invalid_name_starts_with_dash_fails(self, tmp_home: Path) -> None:
        with pytest.raises(typer.Exit) as exc:
            run_add("-leadingdash")
        assert exc.value.exit_code == 1

    def test_valid_name_alphanumeric(self, tmp_home: Path, creds_dir: Path) -> None:
        """Valid names should not fail at validation (may fail later if no server)."""
        _write_proxy_yml(creds_dir)
        inbounds = [_make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[])]
        mock_panel = _make_mock_panel(inbounds)
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
            patch("meridian.commands.client.print_terminal_output"),
            patch("meridian.commands.client.save_connection_html"),
        ):
            # valid names like "alice123", "my-client", "client_1" should pass validation
            run_add("alice123")

    def test_add_sync_failure_restores_local_credentials_and_fails(self, tmp_home: Path, creds_dir: Path) -> None:
        _write_proxy_yml(creds_dir)
        original = (creds_dir / "proxy.yml").read_text()

        inbounds = [
            _make_inbound(id=1, remark="VLESS-Reality", port=443, clients=[]),
            _make_inbound(id=2, remark="VLESS-Reality-XHTTP", port=34567, clients=[]),
        ]
        mock_panel = _make_mock_panel(inbounds, uuid_seq=["new-uuid-1"])
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=False),
            patch("meridian.commands.client.print_terminal_output"),
            patch("meridian.commands.client.save_connection_html"),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_add("alice")
        assert exc.value.exit_code == 1
        assert (creds_dir / "proxy.yml").read_text() == original

    def test_show_recovery_sync_failure_restores_local_credentials_and_fails(
        self, tmp_home: Path, creds_dir: Path
    ) -> None:
        _write_proxy_yml(creds_dir)
        original = (creds_dir / "proxy.yml").read_text()
        mock_resolved = _make_mock_resolved(creds_dir)
        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[{"id": "recovered-uuid", "email": "reality-alice", "flow": "xtls-rprx-vision"}],
            ),
        ]
        mock_panel = _make_mock_panel(inbounds)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc:
                run_show("alice")
        assert exc.value.exit_code == 1
        assert (creds_dir / "proxy.yml").read_text() == original

    def test_remove_partial_panel_failure_warns_but_completes(self, tmp_home: Path, creds_dir: Path) -> None:
        _write_proxy_yml(creds_dir, extra_client="alice")
        inbounds = [
            _make_inbound(
                id=1,
                remark="VLESS-Reality",
                port=443,
                clients=[{"id": "alice-uuid", "email": "reality-alice", "flow": "xtls-rprx-vision"}],
            ),
            _make_inbound(
                id=2,
                remark="VLESS-Reality-XHTTP",
                port=34567,
                clients=[{"id": "alice-uuid", "email": "xhttp-alice", "flow": ""}],
            ),
        ]
        mock_panel = _make_mock_panel(inbounds)
        mock_panel.remove_client.side_effect = [None, PanelError("backend failed")]
        mock_resolved = _make_mock_resolved(creds_dir)

        with (
            patch("meridian.commands.client.ServerRegistry"),
            patch("meridian.commands.client.resolve_server", return_value=mock_resolved),
            patch("meridian.commands.client.ensure_server_connection", return_value=mock_resolved),
            patch("meridian.commands.client.fetch_credentials", return_value=True),
            patch("meridian.commands.client._make_panel", return_value=mock_panel),
            patch("meridian.commands.client._sync_credentials_to_server", return_value=True),
        ):
            run_remove("alice")
        # Client should be removed from local creds despite partial panel failure
        assert "alice" not in (creds_dir / "proxy.yml").read_text()

    def test_invalid_name_remove_fails(self, tmp_home: Path) -> None:
        """Invalid name in remove should also fail at validation."""
        with pytest.raises(typer.Exit) as exc:
            run_remove("bad name!")
        assert exc.value.exit_code == 1
