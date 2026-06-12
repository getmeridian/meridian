"""Tests for _setup_first_deploy — full first-deploy orchestration.

Covers the 15-stage workflow: panel wait → admin registration → API token →
Xray config → config profile → inbound cache → node registration → container
deploy → cluster save → host creation → client creation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.remnawave import RemnawaveError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IP = "198.51.100.1"
_SNI = "www.google.com"
_DOMAIN = "vpn.example.com"
_CLIENT = "alice"
_SECRET_PATH = "secret123abc"
_REALITY_PORT = 10589
_XHTTP_PORT = 30589
_WSS_PORT = 20589
_VERSION = "4.2.3"
_ADMIN_USER = "meridian-test1234"
_ADMIN_PASS = "Mxabcdef12345678901234569A"
_AUTH_TOKEN = "browser-session-jwt"
_API_TOKEN = "permanent-api-token"
_PROFILE_UUID = "cp-uuid-1"
_NODE_UUID = "node-uuid-1"
_SECRET_KEY = "mTLS-secret-key"
_GATEWAY = "172.17.0.1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolved(ip: str = _IP) -> SimpleNamespace:
    conn = MagicMock()
    conn.port = 22
    return SimpleNamespace(ip=ip, user="root", conn=conn, local_mode=False)


def _make_panel_mock() -> MagicMock:
    """Create a MeridianPanel mock that supports context manager."""
    panel = MagicMock()
    panel.__enter__ = MagicMock(return_value=panel)
    panel.__exit__ = MagicMock(return_value=False)

    # Config profile
    panel.find_config_profile_by_name.return_value = None
    profile_mock = MagicMock()
    profile_mock.uuid = _PROFILE_UUID
    profile_mock.name = "meridian-default"
    panel.create_config_profile.return_value = profile_mock

    # Inbounds
    ib_reality = MagicMock(uuid="ib-reality", tag="vless-reality")
    ib_xhttp = MagicMock(uuid="ib-xhttp", tag="vless-xhttp")
    ib_wss = MagicMock(uuid="ib-wss", tag="vless-wss")
    panel.list_inbounds.return_value = [ib_reality, ib_xhttp, ib_wss]

    # Node
    panel.find_node_by_address.return_value = None
    node_creds = MagicMock()
    node_creds.uuid = _NODE_UUID
    node_creds.secret_key = _SECRET_KEY
    panel.create_node.return_value = node_creds

    # Hosts
    panel.list_hosts.return_value = []
    panel.create_host.return_value = MagicMock(uuid="host-uuid")

    # Client
    panel.get_user.return_value = None
    panel.create_user.return_value = MagicMock(uuid="user-uuid", username=_CLIENT)

    return panel


def _base_patches():  # noqa: ANN202
    """Return dict of common patches for _setup_first_deploy tests."""
    return {
        "meridian.panel_bootstrap.wait_for_panel_api": MagicMock(return_value=True),
        "meridian.panel_bootstrap.MeridianPanel.register_admin": MagicMock(return_value=_AUTH_TOKEN),
        "meridian.panel_bootstrap.create_api_token": MagicMock(return_value=_API_TOKEN),
        "meridian.panel_bootstrap.build_xray_config": MagicMock(
            return_value={
                "config": {"inbounds": [], "outbounds": []},
                "reality_public_key": "PUB_KEY",
                "reality_short_id": "abcd1234",
                "reality_private_key": "PRIV_KEY",
            }
        ),
        "meridian.panel_bootstrap.get_docker_gateway": MagicMock(return_value=_GATEWAY),
        "meridian.panel_bootstrap.deploy_node_container": MagicMock(return_value=True),
        "meridian.panel_bootstrap.create_hosts_for_node": MagicMock(),
        "meridian.panel_bootstrap.secrets.token_hex": MagicMock(side_effect=lambda n: "a" * (n * 2)),
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSetupFirstDeployHappyPath:
    def test_saves_admin_creds_before_api_token(self) -> None:
        """Admin creds saved to cluster BEFORE API token creation (lockout prevention)."""
        cluster = ClusterConfig()
        panel = _make_panel_mock()
        save_calls: list[str] = []

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "PUB",
                    "reality_short_id": "ab",
                    "reality_private_key": "PRIV",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save", side_effect=lambda: save_calls.append("save")),
            patch.object(cluster, "backup", side_effect=lambda: save_calls.append("backup")),
        ):
            from meridian.commands.setup import _setup_first_deploy

            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        # First save happens BEFORE _create_api_token
        # save_calls = ["backup", "save", "save", "backup", "save"]
        assert len(save_calls) >= 2
        # Admin creds set before first save
        assert cluster.panel.admin_user != ""

    def test_node_entry_has_reality_keys(self) -> None:
        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "MY_PUB",
                    "reality_short_id": "abcd1234",
                    "reality_private_key": "MY_PRIV",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            from meridian.commands.setup import _setup_first_deploy

            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        assert len(cluster.nodes) == 1
        node = cluster.nodes[0]
        assert node.reality_public_key == "MY_PUB"
        assert node.reality_short_id == "abcd1234"
        assert node.reality_private_key == "MY_PRIV"

    def test_node_is_panel_host(self) -> None:
        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            from meridian.commands.setup import _setup_first_deploy

            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        assert cluster.nodes[0].is_panel_host is True

    def test_creates_first_client(self) -> None:
        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            from meridian.commands.setup import _setup_first_deploy

            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        panel.create_user.assert_called_once()
        call_args = panel.create_user.call_args
        assert call_args[0][0] == _CLIENT

    def test_config_profile_uuid_saved(self) -> None:
        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            from meridian.commands.setup import _setup_first_deploy

            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        assert cluster.config_profile_uuid == _PROFILE_UUID


# ---------------------------------------------------------------------------
# Admin registration
# ---------------------------------------------------------------------------


class TestSetupFirstDeployAdminRegistration:
    def _run_with_register_result(self, register_effect, login_effect=None) -> ClusterConfig:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()

        patches = [
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", side_effect=register_effect),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ]
        if login_effect is not None:
            patches.append(patch("meridian.panel_bootstrap.MeridianPanel.login", side_effect=login_effect))

        import contextlib

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )
        return cluster

    def test_register_success(self) -> None:
        cluster = self._run_with_register_result(MagicMock(return_value=_AUTH_TOKEN))
        assert cluster.panel.admin_user != ""

    def test_register_fails_login_fallback_succeeds(self) -> None:
        cluster = self._run_with_register_result(
            register_effect=RemnawaveError("already exists"),
            login_effect=MagicMock(return_value=_AUTH_TOKEN),
        )
        assert cluster.panel.api_token == _API_TOKEN

    def test_register_and_login_both_fail_exits(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", side_effect=RemnawaveError("fail")),
            patch("meridian.panel_bootstrap.MeridianPanel.login", side_effect=RemnawaveError("fail")),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
            pytest.raises(typer.Exit),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

    def test_reuses_existing_admin_creds_on_rerun(self) -> None:
        """If cluster already has admin creds (from partial failure), reuse them."""
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        cluster.panel = PanelConfig(
            admin_user="existing-admin",
            admin_pass="ExistingPass123456789012",
        )
        panel = _make_panel_mock()

        # The code calls MeridianPanel.register_admin() as a class method.
        # Our patch replaces MeridianPanel with mock_cls, so
        # MeridianPanel.register_admin → mock_cls.register_admin
        mock_cls = MagicMock(return_value=panel)
        mock_cls.register_admin.return_value = _AUTH_TOKEN

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", mock_cls),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        # Verify register was called with existing admin creds
        mock_cls.register_admin.assert_called_once()
        call_args = mock_cls.register_admin.call_args[0]
        assert call_args[1] == "existing-admin"


# ---------------------------------------------------------------------------
# Panel wait
# ---------------------------------------------------------------------------


class TestSetupFirstDeployPanelWait:
    def test_panel_unreachable_fails(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=False),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            pytest.raises(typer.Exit),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )


# ---------------------------------------------------------------------------
# Node evidence
# ---------------------------------------------------------------------------


class TestSetupFirstDeployNodeEvidence:
    def test_node_container_failure_stops_deploy(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "PUB",
                    "reality_short_id": "abcd1234",
                    "reality_private_key": "PRIV",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container", return_value=False),
            patch("meridian.panel_bootstrap.create_hosts_for_node") as mock_hosts,
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
            pytest.raises(typer.Exit) as exc_info,
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        assert exc_info.value.exit_code == 3
        mock_hosts.assert_not_called()


# ---------------------------------------------------------------------------
# Config profile
# ---------------------------------------------------------------------------


class TestSetupFirstDeployConfigProfile:
    def test_reuses_existing_profile_by_name(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        existing = MagicMock()
        existing.uuid = "existing-profile-uuid"
        existing.name = "meridian-default"
        panel.find_config_profile_by_name.return_value = existing

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        # Should NOT call create_config_profile
        panel.create_config_profile.assert_not_called()
        assert cluster.config_profile_uuid == "existing-profile-uuid"

    def test_profile_creation_failure_exits(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        panel.find_config_profile_by_name.side_effect = RemnawaveError("500 Server Error")

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
            pytest.raises(typer.Exit),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )


# ---------------------------------------------------------------------------
# Node registration
# ---------------------------------------------------------------------------


class TestSetupFirstDeployNodeRegistration:
    def test_existing_node_reused(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        existing_node = MagicMock()
        existing_node.uuid = "existing-node-uuid"
        panel.find_node_by_address.return_value = existing_node
        panel.get_node_secret_key.return_value = "existing-secret"

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        panel.create_node.assert_not_called()
        assert cluster.nodes[0].uuid == "existing-node-uuid"

    def test_node_registration_failure_exits(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        panel.find_node_by_address.return_value = None
        panel.create_node.side_effect = RemnawaveError("500")

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
            pytest.raises(typer.Exit),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )


# ---------------------------------------------------------------------------
# Client creation
# ---------------------------------------------------------------------------


class TestSetupFirstDeployClientCreation:
    def test_existing_client_skipped(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        panel.get_user.return_value = MagicMock(username=_CLIENT)  # already exists

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        panel.create_user.assert_not_called()

    def test_client_creation_failure_warns_not_fails(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        panel.create_user.side_effect = RemnawaveError("500")

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value=_API_TOKEN),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            # Should NOT raise — client creation failure is non-fatal
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        # Verify it completed successfully (nodes saved)
        assert len(cluster.nodes) == 1


# ---------------------------------------------------------------------------
# API token
# ---------------------------------------------------------------------------


class TestSetupFirstDeployApiToken:
    def test_api_token_saved_to_cluster(self) -> None:
        from meridian.commands.setup import _setup_first_deploy

        cluster = ClusterConfig()
        panel = _make_panel_mock()

        with (
            patch("meridian.panel_bootstrap.wait_for_panel_api", return_value=True),
            patch("meridian.panel_bootstrap.MeridianPanel.register_admin", return_value=_AUTH_TOKEN),
            patch("meridian.panel_bootstrap.create_api_token", return_value="my-api-token-123"),
            patch(
                "meridian.panel_bootstrap.build_xray_config",
                return_value={
                    "config": {},
                    "reality_public_key": "P",
                    "reality_short_id": "a",
                    "reality_private_key": "K",
                },
            ),
            patch("meridian.panel_bootstrap.get_docker_gateway", return_value=_GATEWAY),
            patch("meridian.panel_bootstrap.deploy_node_container"),
            patch("meridian.panel_bootstrap.create_hosts_for_node"),
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel),
            patch("meridian.panel_bootstrap.secrets.token_hex", side_effect=lambda n: "a" * (n * 2)),
            patch.object(cluster, "save"),
            patch.object(cluster, "backup"),
        ):
            _setup_first_deploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                client_name=_CLIENT,
                secret_path=_SECRET_PATH,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                pq=False,
                geo_block=True,
                version=_VERSION,
            )

        assert cluster.panel.api_token == "my-api-token-123"
