"""Tests for client add/list/show/remove/enable/disable commands.

Clients are the access users declared in the saved topology; runtime user
state lives in Remnawave and is read through MeridianPanel.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_add, run_disable, run_enable, run_list, run_remove, run_show
from meridian.commands.client_handoff import print_handoff_links
from meridian.console import set_json_mode
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.handoff import HandoffLinks
from meridian.remnawave import RemnawaveError, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_v4_cluster(tmp_path: Path, *, users: list[str] | None = None) -> ClusterConfig:
    """Create a configured cluster whose topology declares the given access users."""
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="test-jwt-token",
            server_ip="198.51.100.1",
            sub_path="pages",
        ),
        topology_intent=SetupIntent(
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
        ),
    )
    cluster.save(tmp_path / "cluster.yml")
    return cluster


def _make_cluster(tmp_path: Path) -> ClusterConfig:
    """Create a configured cluster whose topology declares alice and bob."""
    return _make_v4_cluster(tmp_path, users=["alice", "bob"])


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
            patch(
                "meridian.commands.client._publish_handoff",
                return_value=[HandoffLinks("alice", subscription_url="https://198.51.100.1/pages/sub/abc123")],
            ) as publish_handoff,
        ):
            run_add(names=["alice"])

        updated = runtime.apply_intent.call_args.args[0]
        assert updated.access.users == ["default", "alice"]
        panel.create_user.assert_not_called()
        publish_handoff.assert_called_once_with(cluster, ["alice"])

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

    def test_add_duplicate_in_batch_fails(self, tmp_home: Path) -> None:
        """Duplicate names within the same batch should fail validation."""
        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = _make_cluster(tmp_home)
            run_add(names=["alice", "alice"])

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


class TestShowConnectionPage:
    def test_show_advertises_only_a_published_page(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from meridian.pwa import mark_connection_page_deployed

        cluster = _make_cluster(tmp_home)
        user = _make_user("alice")
        panel = _make_panel_mock()
        panel.get_user.return_value = user

        for published in (False, True):
            if published:
                mark_connection_page_deployed(cluster, user.vless_uuid)
            set_json_mode(True)
            try:
                with (
                    patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                    patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                ):
                    run_show(name="alice")
            finally:
                set_json_mode(False)

            payload = json.loads(capsys.readouterr().out)
            assert payload["data"]["handoff"]["share_available"] is published

    def test_show_repair_page_republishes_from_the_subscription(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        panel = _make_panel_mock()
        page_url = "https://198.51.100.1/pages/550e8400-e29b-41d4-a716-446655440099/"

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch(
                "meridian.handoff.publish_connection_pages",
                return_value=[HandoffLinks("alice", page_url=page_url)],
            ) as publish,
            patch("meridian.commands.client._print_handoff_links") as print_links,
        ):
            run_show(name="alice", repair_page=True)

        publish.assert_called_once_with(cluster, ["alice"])
        assert print_links.call_args.kwargs["page_url"] == page_url

    def test_show_failed_explicit_page_repair_returns_partial_json(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _make_cluster(tmp_home)
        panel = _make_panel_mock()

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch(
                    "meridian.handoff.publish_connection_pages",
                    return_value=[HandoffLinks("alice", error="upload failed")],
                ),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_show(name="alice", repair_page=True)
        finally:
            set_json_mode(False)

        assert exc_info.value.exit_code == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["data"]["handoff"]["share_available"] is False
        assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_PAGE_REPAIR_FAILED"

    def test_show_repair_save_failure_reports_remote_page_change(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _make_cluster(tmp_home)
        panel = _make_panel_mock()
        page_url = "https://198.51.100.1/pages/550e8400-e29b-41d4-a716-446655440099/"

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch(
                    "meridian.handoff.publish_connection_pages",
                    return_value=[HandoffLinks("alice", page_url=page_url)],
                ),
                patch.object(cluster, "save", side_effect=OSError("read-only filesystem")),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_show(name="alice", repair_page=True)
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert exc_info.value.exit_code == 3
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
        cluster = _make_cluster(tmp_home)
        assert cluster.topology_intent is not None
        cluster.topology_intent = cluster.topology_intent.model_copy(
            update={"access": AccessIntent.model_construct(users=["[red]visible[/red]"])}
        )

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
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


class TestPanelAPIErrors:
    @pytest.mark.parametrize("command", ["add", "enable", "disable"])
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
                    run_add(names=["carol"], json_mode=True)
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


class TestRunRemoveJson:
    def test_remove_json_refusal_emits_error_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """client remove --json emits an error envelope for the unavailable removal."""
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
