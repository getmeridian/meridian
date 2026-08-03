"""Tests for client add/list/show/remove commands.

4.0: All client state lives in Remnawave's database.
Commands call the Remnawave REST API via MeridianPanel.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig
from meridian.commands.client import run_add, run_disable, run_enable, run_list, run_remove, run_show
from meridian.commands.client_handoff import PageCleanup, cleanup_client_page, print_handoff_links
from meridian.console import set_json_mode
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.remnawave import RemnawaveError, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cluster(tmp_path: Path) -> ClusterConfig:
    """Create a minimal configured cluster."""
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="test-jwt-token",
            server_ip="198.51.100.1",
        ),
    )
    cluster.save(tmp_path / "cluster.yml")
    return cluster


def _make_v4_cluster(tmp_path: Path, *, users: list[str] | None = None) -> ClusterConfig:
    cluster = _make_cluster(tmp_path)
    cluster.topology_intent = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="reality-a",
                        protocol="reality",
                        reality_sni="www.example.com",
                    )
                ],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=users or ["default"]),
    )
    return cluster


def _make_user(name: str = "alice", status: str = "ACTIVE") -> User:
    return User(
        uuid="550e8400-e29b-41d4-a716-446655440000",
        short_uuid="abc123",
        vless_uuid="550e8400-e29b-41d4-a716-446655440099",
        username=name,
        status=status,
        used_traffic_bytes=1024 * 1024 * 100,  # 100 MB
        created_at="2026-04-01T12:00:00Z",
    )


def _make_panel_mock() -> MagicMock:
    """Create a mock MeridianPanel."""
    panel = MagicMock()
    panel.__enter__ = MagicMock(return_value=panel)
    panel.__exit__ = MagicMock(return_value=False)
    panel.create_user.return_value = _make_user()
    panel.get_user.return_value = _make_user()
    panel.list_users.return_value = [_make_user("alice"), _make_user("bob")]
    panel.delete_user.return_value = True
    panel.get_subscription_url.return_value = "https://198.51.100.1/api/sub/abc123"
    panel.ping.return_value = True
    return panel


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunAdd:
    def test_v4_add_updates_intent_and_uses_compiled_access_driver(self, tmp_home: Path) -> None:
        cluster = _make_v4_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, _make_user("alice")]
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, changed=True, failed=[])

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
            patch("meridian.servers.ServerRegistry"),
            patch("meridian.commands.client._print_subscription") as print_subscription,
        ):
            run_add(names=["alice"])

        updated = runtime.apply_intent.call_args.args[0]
        assert updated.access.users == ["default", "alice"]
        panel.create_user.assert_not_called()
        print_subscription.assert_called_once()

    def test_v4_add_save_failure_returns_observed_partial_json(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from meridian.commands._helpers import ReviewedApplyPersistenceError

        cluster = _make_v4_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, _make_user("alice")]
        runtime = MagicMock()
        runtime.apply_intent.side_effect = ReviewedApplyPersistenceError("disk full; remote state may have changed")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
                patch("meridian.servers.ServerRegistry"),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_add(names=["alice"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["data"]["clients"][0]["username"] == "alice"
        assert payload["warnings"][0]["details"]["remote_state_changed"] is True
        assert "Remote state changed" in payload["warnings"][0]["message"]

    def test_v4_add_save_failure_without_observation_warns_remote_may_have_changed(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from meridian.commands._helpers import ReviewedApplyPersistenceError

        cluster = _make_v4_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, None]
        runtime = MagicMock()
        runtime.apply_intent.side_effect = ReviewedApplyPersistenceError(
            "state file is read-only; remote state may have changed"
        )

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
                patch("meridian.servers.ServerRegistry"),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_add(names=["alice"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "system"
        assert "remote state may have changed" in payload["errors"][0]["message"]

    def test_add_client_success(self, tmp_home: Path) -> None:
        """Adding a client calls panel.create_user and prints subscription URL."""
        _make_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.return_value = None  # no existing user with this name

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice"])

        panel.create_user.assert_called_once_with("alice", squad_uuids=None)

    def test_add_multiple_clients(self, tmp_home: Path) -> None:
        """Adding multiple clients creates each one via panel.create_user."""
        _make_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        users = {
            "alice": _make_user("alice"),
            "bob": _make_user("bob"),
        }
        panel.create_user.side_effect = lambda name, **kw: users[name]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice", "bob"])

        assert panel.create_user.call_count == 2

    def test_add_uses_recovered_access_squad(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.squad_uuid = "990e8400-e29b-41d4-a716-446655440004"
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            run_add(names=["alice"])

        panel.create_user.assert_called_once_with(
            "alice",
            squad_uuids=["990e8400-e29b-41d4-a716-446655440004"],
        )

    def test_add_duplicate_in_batch_fails(self, tmp_home: Path) -> None:
        """Duplicate names within the same batch should fail validation."""
        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice", "alice"])

    def test_add_duplicate_client_fails(self, tmp_home: Path) -> None:
        """Adding a client that already exists should fail."""
        panel = _make_panel_mock()
        # User already exists
        panel.get_user.return_value = _make_user("alice")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice"])

        panel.create_user.assert_not_called()

    def test_add_client_empty_name_fails(self, tmp_home: Path) -> None:
        """Empty client name should fail."""
        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=[""])

    def test_add_client_no_cluster_fails(self, tmp_home: Path) -> None:
        """Adding a client without a configured cluster should fail."""
        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = ClusterConfig()  # unconfigured
            run_add(names=["alice"])

    def test_add_partial_failure_reports_both(self, tmp_home: Path) -> None:
        """If one client fails, successes are still reported."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        def _create(name: str, **kw: object) -> User:
            if name == "bob":
                raise RemnawaveError("Panel unreachable")
            return _make_user(name)

        panel.create_user.side_effect = _create

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice", "bob"])

        assert exc_info.value.exit_code == 3
        assert panel.create_user.call_count == 2


class TestRunShow:
    def test_handoff_urls_render_as_plain_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        subscription_url = "https://[2001:db8::1]/[red]subscription[/red]"
        page_url = "https://[2001:db8::2]/[bold]page[/bold]"

        with patch("meridian.urls.generate_qr_terminal", return_value=""):
            print_handoff_links(subscription_url, page_url=page_url)

        output = capsys.readouterr().err
        assert subscription_url in output
        assert page_url in output

    def test_show_existing_client(self, tmp_home: Path) -> None:
        """Showing an existing client prints subscription URL."""
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_show(name="alice")

        panel.get_user.assert_called_once_with("alice")

    def test_v4_show_does_not_expose_internal_service_user(self, tmp_home: Path) -> None:
        cluster = _make_v4_cluster(tmp_home, users=["alice"])
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_show(name="meridian-route-123")

        assert exc_info.value.exit_code == 2
        panel.get_user.assert_not_called()

    def test_show_never_prints_panel_admin_credentials(
        self,
        tmp_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.admin_user = "panel-admin"
        cluster.panel.admin_pass = "panel-password"

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            run_show(name="alice")

        output = capsys.readouterr().err
        assert "panel-admin" not in output
        assert "panel-password" not in output

    def test_show_nonexistent_client_fails(self, tmp_home: Path) -> None:
        """Showing a non-existent client should fail."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_show(name="nonexistent")

    def test_show_json_redacts_subscription_url(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Legacy JSON renderer must still apply central redaction."""
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_show(name="alice")
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.show"
        assert payload["data"]["handoff"]["subscription_available"] is True
        assert payload["data"]["handoff"]["redacted"] is True
        assert "subscription_url" not in payload["data"]["client"]
        assert "abc123" not in json.dumps(payload)

    def test_show_does_not_advertise_unconfirmed_connection_page(
        self,
        tmp_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True)]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.run.return_value = SimpleNamespace(returncode=1)
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
            ):
                run_show(name="alice")
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["handoff"]["share_available"] is False

    def test_show_does_not_persist_missing_page_evidence_on_ssh_loss(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from meridian.pwa import connection_page_failed

        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True)]
        user = _make_user("alice")
        panel.get_user.return_value = user
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.run.return_value = SimpleNamespace(returncode=255)

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
            ):
                run_show(name="alice")
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["handoff"]["share_available"] is False
        assert connection_page_failed(cluster, user.vless_uuid) is False

    def test_show_observed_page_save_failure_returns_partial_json(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True)]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.run.return_value = SimpleNamespace(returncode=0)

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
                patch.object(cluster, "save", side_effect=OSError("read-only filesystem")),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_show(name="alice")
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["data"]["handoff"]["share_available"] is True
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_LOCAL_STATE_SAVE_FAILED"
        assert payload["warnings"][0]["details"]["remote_state_changed"] is False

    def test_show_repair_page_regenerates_failed_legacy_page(self, tmp_home: Path) -> None:
        from meridian.pwa import connection_page_deployed, mark_connection_page_failed

        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True, reality_public_key="public-key")]
        user = _make_user("alice")
        panel.get_user.return_value = user
        mark_connection_page_failed(cluster, user.vless_uuid)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        repaired_url = f"https://198.51.100.1/share/{user.vless_uuid}/"

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
            patch("meridian.pwa.deploy_client_page", return_value=repaired_url) as deploy,
            patch("meridian.commands.client._print_handoff_links"),
        ):
            run_show(name="alice", repair_page=True)

        deploy.assert_called_once()
        assert connection_page_deployed(cluster, user.vless_uuid) is True

    def test_show_failed_explicit_page_repair_returns_partial_json(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from meridian.pwa import mark_connection_page_failed

        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True)]
        user = _make_user("alice")
        panel.get_user.return_value = user
        mark_connection_page_failed(cluster, user.vless_uuid)
        connection = MagicMock()
        connection.__enter__.return_value = connection

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
                patch("meridian.pwa.deploy_client_page", return_value=""),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_show(name="alice", repair_page=True)
        finally:
            set_json_mode(False)

        assert exc_info.value.exit_code == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 3
        assert payload["data"]["handoff"]["share_available"] is False
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_PAGE_REPAIR_FAILED"

    def test_show_repair_save_failure_reports_remote_page_change(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from meridian.pwa import mark_connection_page_failed

        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [NodeEntry(ip="198.51.100.1", is_panel_host=True)]
        user = _make_user("alice")
        panel.get_user.return_value = user
        mark_connection_page_failed(cluster, user.vless_uuid)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        repaired_url = f"https://198.51.100.1/share/{user.vless_uuid}/"

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client_handoff.ServerConnection", return_value=connection),
                patch("meridian.pwa.deploy_client_page", return_value=repaired_url),
                patch.object(cluster, "save", side_effect=OSError("read-only filesystem")),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_show(name="alice", repair_page=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["handoff"]["share_available"] is True
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_LOCAL_STATE_SAVE_FAILED"
        assert payload["warnings"][0]["details"]["remote_state_changed"] is True


class TestRunList:
    def test_list_renders_panel_username_as_plain_text(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.list_users.return_value = [_make_user("[red]visible[/red]")]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=_make_cluster(tmp_home)),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            run_list()

        assert "[red]visible[/red]" in capsys.readouterr().err

    def test_list_clients(self, tmp_home: Path) -> None:
        """Listing clients calls panel.list_users."""
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_list()

        panel.list_users.assert_called_once()

    def test_v4_list_hides_internal_service_users(
        self,
        tmp_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = _make_v4_cluster(tmp_home, users=["alice"])
        panel = _make_panel_mock()
        panel.list_users.return_value = [_make_user("alice"), _make_user("meridian-route-123")]
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["summary"]["clients"] == 1
        assert [client["username"] for client in payload["data"]["clients"]] == ["alice"]

    def test_list_empty_clients(self, tmp_home: Path) -> None:
        """Listing clients when none exist should still succeed."""
        panel = _make_panel_mock()
        panel.list_users.return_value = []

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_list()

    def test_list_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Client list JSON uses the standard output envelope."""
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.list"
        assert payload["data"]["summary"]["clients"] == 2
        assert payload["data"]["clients"][0]["traffic_used_bytes"] == 104857600


class TestRunRemove:
    def test_page_cleanup_transport_failure_is_not_silent(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"

        with patch("meridian.commands.client_handoff.ServerConnection", side_effect=OSError("SSH unavailable")):
            result = cleanup_client_page(_make_user("alice"), cluster)

        assert result.state_changed is False
        assert result.error == "SSH unavailable"

    def test_v4_remove_refuses_direct_panel_delete(self, tmp_home: Path) -> None:
        cluster = _make_v4_cluster(tmp_home, users=["alice"])
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel") as panel,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove(name="alice", yes=True)

        assert exc_info.value.exit_code == 2
        panel.assert_not_called()

    def test_remove_existing_client(self, tmp_home: Path) -> None:
        """Removing a client calls panel.delete_user."""
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.client.confirm", return_value=True),
            patch("meridian.commands.client._cleanup_client_page", return_value=PageCleanup()),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_remove(name="alice")

        panel.delete_user.assert_called_once()

    def test_remove_reports_every_incomplete_follow_up(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.desired_clients = ["alice"]
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch.object(cluster, "save", side_effect=OSError("disk full")),
            patch(
                "meridian.commands.client._cleanup_client_page",
                return_value=PageCleanup(error="SSH unavailable"),
            ),
            patch("meridian.commands.client.warn") as warning,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove(name="alice", yes=True)

        assert exc_info.value.exit_code == 3
        assert warning.call_count == 2
        assert "could not save" in warning.call_args_list[0].args[0]
        assert "cleanup could not be confirmed" in warning.call_args_list[1].args[0]

    def test_remove_nonexistent_client_fails(self, tmp_home: Path) -> None:
        """Removing a non-existent client should fail."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_remove(name="nonexistent")

    def test_remove_cancelled_by_user(self, tmp_home: Path) -> None:
        """User declining confirmation should not delete."""
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.client.confirm", return_value=False),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_remove(name="alice")

        panel.delete_user.assert_not_called()


class TestPanelAPIErrors:
    def test_api_error_on_add_shows_message(self, tmp_home: Path) -> None:
        """RemnawaveError during add should be caught and shown to user."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        panel.create_user.side_effect = RemnawaveError("Panel unreachable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice"])

    @pytest.mark.parametrize("command", ["add", "remove", "enable", "disable"])
    def test_lookup_error_emits_typed_json_failure(
        self,
        command: str,
        tmp_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        panel = _make_panel_mock()
        panel.get_user.side_effect = RemnawaveError("Panel lookup unavailable")
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=_make_cluster(tmp_home)),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit) as exc_info,
            ):
                if command == "add":
                    run_add(names=["alice"], json_mode=True)
                elif command == "remove":
                    run_remove(name="alice", yes=True, json_mode=True)
                elif command == "enable":
                    run_enable(name="alice", json_mode=True)
                else:
                    run_disable(name="alice", json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["command"] == f"client.{command}"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "system"

    def test_add_handoff_failure_keeps_created_user_and_returns_partial(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        panel.get_subscription_url.side_effect = RemnawaveError("handoff unavailable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=_make_cluster(tmp_home)),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(names=["alice"])

        assert exc_info.value.exit_code == 3
        panel.create_user.assert_called_once()


class TestRunEnable:
    def test_enable_existing_client(self, tmp_home: Path) -> None:
        """Enabling a client calls panel.enable_user with the user's UUID."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="DISABLED")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_enable(name="alice")

        panel.get_user.assert_called_once_with("alice")
        panel.enable_user.assert_called_once_with("550e8400-e29b-41d4-a716-446655440000")

    def test_enable_nonexistent_client_fails(self, tmp_home: Path) -> None:
        """Enabling a non-existent client should fail with user hint."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_enable(name="nonexistent")

        panel.enable_user.assert_not_called()

    def test_enable_api_error_shows_message(self, tmp_home: Path) -> None:
        """RemnawaveError during enable should be caught and shown to user."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="DISABLED")
        panel.enable_user.side_effect = RemnawaveError("Panel unreachable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_enable(name="alice")


class TestRunDisable:
    def test_v4_disable_is_explicitly_temporary(self, tmp_home: Path) -> None:
        cluster = _make_v4_cluster(tmp_home, users=["alice"])
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.client.warn") as warning,
        ):
            run_disable(name="alice")

        panel.disable_user.assert_called_once()
        warning.assert_called_once_with("This V4 client is disabled only until the next `meridian apply`")

    def test_v4_disable_rejects_unmanaged_panel_user(self, tmp_home: Path) -> None:
        cluster = _make_v4_cluster(tmp_home, users=["default"])
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel") as panel,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_disable(name="alice")

        assert exc_info.value.exit_code == 2
        panel.assert_not_called()

    def test_disable_existing_client(self, tmp_home: Path) -> None:
        """Disabling a client calls panel.disable_user with the user's UUID."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="ACTIVE")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_disable(name="alice")

        panel.get_user.assert_called_once_with("alice")
        panel.disable_user.assert_called_once_with("550e8400-e29b-41d4-a716-446655440000")

    def test_disable_nonexistent_client_fails(self, tmp_home: Path) -> None:
        """Disabling a non-existent client should fail with user hint."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_disable(name="nonexistent")

        panel.disable_user.assert_not_called()

    def test_disable_api_error_shows_message(self, tmp_home: Path) -> None:
        """RemnawaveError during disable should be caught and shown to user."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="ACTIVE")
        panel.disable_user.side_effect = RemnawaveError("Panel unreachable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_disable(name="alice")


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestRunAddJson:
    def test_add_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client add --json produces a meridian.output/v1 envelope."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_add(names=["alice"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.add"
        assert payload["summary"]["changed"] is True
        assert payload["summary"]["counts"]["added"] == 1
        assert len(payload["data"]["clients"]) == 1
        assert payload["data"]["clients"][0]["username"] == "alice"
        assert payload["data"]["clients"][0]["status"] == "active"

    def test_add_multiple_json_outputs_all_clients(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client add --json with multiple names lists all created clients."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        users = {
            "alice": _make_user("alice"),
            "bob": _make_user("bob"),
        }
        panel.create_user.side_effect = lambda name, **kw: users[name]

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_add(names=["alice", "bob"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "client.add"
        assert len(payload["data"]["clients"]) == 2
        assert payload["summary"]["counts"]["added"] == 2

    def test_add_json_error_envelope_on_failure(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client add --json emits error envelope when user already exists."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_add(names=["alice"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.add"
        assert payload["status"] == "failed"
        assert len(payload["errors"]) >= 1

    def test_add_json_partial_success_is_nonzero_and_typed(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        def create(name: str, **_kwargs: object) -> User:
            if name == "bob":
                raise RemnawaveError("request failed at https://198.51.100.1/secret-path")
            return _make_user(name)

        panel.create_user.side_effect = create
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit) as exc_info,
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_add(names=["alice", "bob"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 3
        assert payload["summary"]["counts"] == {"added": 1, "failed": 1, "page_deploy_failed": 0}
        assert payload["data"]["clients"][0]["username"] == "alice"
        assert payload["warnings"][0]["details"]["client"] == "bob"
        assert "secret-path" not in json.dumps(payload)

    def test_add_json_local_save_failure_preserves_remote_result(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.desired_clients = []
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch.object(cluster, "save", side_effect=OSError("disk full")),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_add(names=["alice"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 3
        assert payload["data"]["clients"][0]["username"] == "alice"
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_LOCAL_STATE_SAVE_FAILED"
        assert payload["warnings"][0]["details"]["remote_state_changed"] is True
        assert "Remote state changed" in payload["warnings"][0]["message"]
        panel.create_user.assert_called_once()

    def test_add_json_retries_earlier_save_failure_after_later_noop_sync(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.desired_clients = ["bob"]
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch.object(cluster, "save", side_effect=[OSError("disk full"), None]) as save,
            ):
                run_add(names=["alice", "bob"], json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 0
        assert save.call_count == 2
        assert cluster.desired_clients == ["bob", "alice"]

    def test_add_page_deploy_failure_uses_subscription_and_returns_partial(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.panel.sub_path = "share"
        cluster.nodes = [
            NodeEntry(
                ip="198.51.100.1",
                uuid="550e8400-e29b-41d4-a716-446655440001",
                is_panel_host=True,
            )
        ]
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        connection = MagicMock()
        connection.__enter__.return_value = connection

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=connection),
            patch("meridian.pwa.deploy_client_page", return_value=""),
            patch("meridian.commands.client._print_subscription") as print_subscription,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(names=["alice"])

        assert exc_info.value.exit_code == 3
        print_subscription.assert_called_once()
        assert print_subscription.call_args.kwargs["page_url"] == ""


class TestRunRemoveJson:
    def test_remove_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client remove --json produces a meridian.output/v1 envelope."""
        panel = _make_panel_mock()

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client._cleanup_client_page", return_value=PageCleanup()),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_remove(name="alice", yes=True, json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.remove"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["client"]["username"] == "alice"

    def test_remove_json_local_save_failure_reports_remote_change(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _make_cluster(tmp_home)
        cluster.desired_clients = ["alice"]
        panel = _make_panel_mock()

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client._cleanup_client_page", return_value=PageCleanup()),
                patch.object(cluster, "save", side_effect=OSError("permission denied")),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_remove(name="alice", yes=True, json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 3
        assert payload["data"]["client"]["username"] == "alice"
        assert payload["warnings"][0]["category"] == "system"
        assert "Remote state changed" in payload["warnings"][0]["message"]
        panel.delete_user.assert_called_once()

    def test_remove_json_page_cleanup_failure_is_partial(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=_make_cluster(tmp_home)),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch(
                    "meridian.commands.client._cleanup_client_page",
                    return_value=PageCleanup(error="SSH connection unavailable"),
                ),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_remove(name="alice", yes=True, json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == "ok"
        assert payload["data"]["client"]["username"] == "alice"
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_PAGE_CLEANUP_FAILED"
        assert payload["warnings"][0]["details"]["remote_state_changed"] is True

    def test_remove_json_error_on_not_found(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client remove --json emits error envelope when client not found."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_remove(name="nonexistent", yes=True, json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["command"] == "client.remove"

    def test_remove_json_requires_yes_without_prompting(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.client.confirm") as mock_confirm,
                pytest.raises(typer.Exit) as exc_info,
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_remove(name="alice", yes=False, json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 2
        assert payload["status"] == "failed"
        assert "--yes" in payload["errors"][0]["hint"]
        mock_confirm.assert_not_called()
        panel.delete_user.assert_not_called()


class TestRunEnableJson:
    def test_enable_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client enable --json produces a meridian.output/v1 envelope."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="DISABLED")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_enable(name="alice", json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.enable"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["client"]["username"] == "alice"
        assert payload["data"]["client"]["status"] == "active"

    def test_enable_json_error_on_not_found(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client enable --json emits error envelope when client not found."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_enable(name="nonexistent", json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["command"] == "client.enable"


class TestRunDisableJson:
    def test_disable_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client disable --json produces a meridian.output/v1 envelope."""
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status="ACTIVE")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_disable(name="alice", json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.disable"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["client"]["username"] == "alice"
        assert payload["data"]["client"]["status"] == "disabled"

    def test_disable_json_error_on_not_found(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client disable --json emits error envelope when client not found."""
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit),
            ):
                mock_load.return_value = _make_cluster(tmp_home)
                run_disable(name="nonexistent", json_mode=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["command"] == "client.disable"
