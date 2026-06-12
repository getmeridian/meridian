"""Tests for _setup_redeploy — redeploy orchestration.

Covers the workflow: API token check → node lookup → panel ping →
Xray config build (key reuse vs regeneration) → config profile →
inbound cache → node verification → container deploy → host creation →
metadata update → cluster save.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, InboundRef, NodeEntry, PanelConfig, ProtocolKey
from meridian.remnawave import RemnawaveAuthError, RemnawaveError
from meridian.xray_config import XrayConfigResult

# ---------------------------------------------------------------------------
# Constants (RFC 5737 IPs)
# ---------------------------------------------------------------------------

_IP = "198.51.100.1"
_SNI = "www.google.com"
_REALITY_PORT = 10589
_XHTTP_PORT = 30589
_WSS_PORT = 20589
_VERSION = "4.2.3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolved(ip: str = _IP) -> SimpleNamespace:
    conn = MagicMock()
    conn.port = 22
    return SimpleNamespace(ip=ip, user="root", conn=conn, local_mode=False)


def _configured_cluster(*, with_keys: bool = True) -> ClusterConfig:
    node = NodeEntry(
        ip=_IP,
        uuid="node-uuid-1",
        is_panel_host=True,
        sni="www.google.com",
        xhttp_path="existing_xhttp",
        ws_path="existing_ws",
    )
    if with_keys:
        node.reality_private_key = "EXISTING_PRIV"
        node.reality_public_key = "EXISTING_PUB"
        node.reality_short_id = "deadbeef"
    return ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="test-jwt-token",
            server_ip=_IP,
            secret_path="secret_path",
        ),
        config_profile_uuid="cp-uuid-1",
        config_profile_name="meridian-default",
        nodes=[node],
        inbounds={
            ProtocolKey.REALITY: InboundRef(uuid="ib-reality", tag="vless-reality"),
        },
    )


def _make_panel_mock() -> MagicMock:
    """Create a MeridianPanel mock that supports context manager."""
    panel = MagicMock()
    panel.__enter__ = MagicMock(return_value=panel)
    panel.__exit__ = MagicMock(return_value=False)
    panel.ping.return_value = True
    panel.get_node.return_value = MagicMock(uuid="node-uuid-1")

    # Config profile
    existing_profile = MagicMock()
    existing_profile.uuid = "cp-uuid-1"
    existing_profile.name = "meridian-default"
    panel.find_config_profile_by_name.return_value = existing_profile

    new_profile = MagicMock()
    new_profile.uuid = "cp-uuid-2"
    new_profile.name = f"meridian-default-{_VERSION}"
    panel.create_config_profile.return_value = new_profile

    # Keygen for container redeploy
    panel.get_node_secret_key.return_value = "new-secret-key"

    # Hosts
    panel.list_hosts.return_value = []
    panel.create_host.return_value = MagicMock(uuid="host-uuid")

    return panel


_XRAY_RESULT = XrayConfigResult(
    config={"inbounds": [], "outbounds": []},
    reality_public_key="NEW_PUB",
    reality_short_id="aabb1122",
    reality_private_key="NEW_PRIV",
)


def _run_redeploy(
    cluster: ClusterConfig | None = None,
    panel_mock: MagicMock | None = None,
    xray_result: XrayConfigResult | None = None,
    resolved: SimpleNamespace | None = None,
    *,
    sni: str = _SNI,
    domain: str = "",
    xhttp_path: str = "",
    ws_path: str = "",
) -> dict:
    """Run _setup_redeploy with standard mocks. Returns dict of mocks + cluster."""
    from meridian.panel_bootstrap import setup_redeploy

    if cluster is None:
        cluster = _configured_cluster()
    if panel_mock is None:
        panel_mock = _make_panel_mock()
    if resolved is None:
        resolved = _make_resolved()
    if xray_result is None:
        xray_result = _XRAY_RESULT

    mocks: dict[str, MagicMock] = {}

    with (
        patch("meridian.panel_bootstrap.MeridianPanel", return_value=panel_mock),
        patch("meridian.panel_bootstrap.build_xray_config", return_value=xray_result) as mock_build,
        patch("meridian.panel_bootstrap.deploy_node_container", return_value=True) as mock_deploy,
        patch("meridian.panel_bootstrap.create_hosts_for_node") as mock_hosts,
        patch("meridian.panel_bootstrap.cache_inbounds") as mock_cache,
        patch.object(cluster, "save") as mock_save,
        patch.object(cluster, "backup") as mock_backup,
    ):
        mocks["build_xray_config"] = mock_build
        mocks["deploy_node_container"] = mock_deploy
        mocks["create_hosts"] = mock_hosts
        mocks["cache_inbounds"] = mock_cache
        mocks["save"] = mock_save
        mocks["backup"] = mock_backup

        setup_redeploy(
            resolved=resolved,
            cluster=cluster,
            domain=domain,
            sni=sni,
            reality_port=_REALITY_PORT,
            xhttp_port=_XHTTP_PORT,
            wss_port=_WSS_PORT,
            version=_VERSION,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )

    mocks["cluster"] = cluster
    mocks["panel"] = panel_mock
    return mocks


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSetupRedeployHappyPath:
    def test_full_redeploy_preserves_reality_keys(self) -> None:
        """When all 3 reality keys present, they are passed to _build_xray_config."""
        result = _run_redeploy()
        build = result["build_xray_config"]
        build.assert_called_once()
        _, kwargs = build.call_args
        assert kwargs["existing_private_key"] == "EXISTING_PRIV"
        assert kwargs["existing_public_key"] == "EXISTING_PUB"
        assert kwargs["existing_short_id"] == "deadbeef"

    def test_redeploy_updates_node_sni(self) -> None:
        new_sni = "cdn.example.com"
        result = _run_redeploy(sni=new_sni)
        node = result["cluster"].nodes[0]
        assert node.sni == new_sni

    def test_redeploy_updates_node_domain(self) -> None:
        result = _run_redeploy(domain="vpn.example.com")
        node = result["cluster"].nodes[0]
        assert node.domain == "vpn.example.com"

    def test_redeploy_saves_cluster(self) -> None:
        result = _run_redeploy()
        result["backup"].assert_called_once()
        result["save"].assert_called_once()

    def test_redeploy_updates_deployed_with_version(self) -> None:
        result = _run_redeploy()
        node = result["cluster"].nodes[0]
        assert node.deployed_with == _VERSION

    def test_redeploy_calls_cache_inbounds(self) -> None:
        result = _run_redeploy()
        result["cache_inbounds"].assert_called_once()

    def test_redeploy_calls_create_hosts(self) -> None:
        result = _run_redeploy()
        result["create_hosts"].assert_called_once()

    def test_redeploy_calls_deploy_node_container(self) -> None:
        """Container redeployed with new secret key from keygen."""
        result = _run_redeploy()
        result["deploy_node_container"].assert_called()

    def test_redeploy_updates_xhttp_path(self) -> None:
        result = _run_redeploy(xhttp_path="new_xhttp")
        node = result["cluster"].nodes[0]
        assert node.xhttp_path == "new_xhttp"

    def test_redeploy_updates_ws_path(self) -> None:
        result = _run_redeploy(ws_path="new_ws")
        node = result["cluster"].nodes[0]
        assert node.ws_path == "new_ws"

    def test_redeploy_preserves_existing_paths_when_empty(self) -> None:
        """When xhttp_path/ws_path are empty strings, existing paths are reused."""
        result = _run_redeploy(xhttp_path="", ws_path="")
        build = result["build_xray_config"]
        _, kwargs = build.call_args
        assert kwargs["xhttp_path"] == "existing_xhttp"
        assert kwargs["ws_path"] == "existing_ws"


# ---------------------------------------------------------------------------
# Key preservation / regeneration
# ---------------------------------------------------------------------------


class TestSetupRedeployKeyPreservation:
    def test_reuses_keys_when_all_three_present(self) -> None:
        """When node has all 3 reality keys, existing_*_key params are passed."""
        result = _run_redeploy()
        build = result["build_xray_config"]
        _, kwargs = build.call_args
        assert kwargs["existing_private_key"] == "EXISTING_PRIV"
        assert kwargs["existing_public_key"] == "EXISTING_PUB"
        assert kwargs["existing_short_id"] == "deadbeef"
        # First arg (conn) should be None when reusing keys
        args, _ = build.call_args
        assert args[0] is None

    def test_regenerates_keys_when_private_missing(self) -> None:
        """When no saved keys, _build_xray_config called without existing_*."""
        cluster = _configured_cluster(with_keys=False)
        resolved = _make_resolved()
        result = _run_redeploy(cluster=cluster, resolved=resolved)
        build = result["build_xray_config"]
        _, kwargs = build.call_args
        assert "existing_private_key" not in kwargs
        assert "existing_public_key" not in kwargs
        assert "existing_short_id" not in kwargs
        # First arg should be the conn (SSH needed for keygen)
        args, _ = build.call_args
        assert args[0] is resolved.conn

    def test_regenerates_when_public_key_missing(self) -> None:
        """If only private key is set but public key is missing, regenerate."""
        cluster = _configured_cluster(with_keys=False)
        cluster.nodes[0].reality_private_key = "SOME_PRIV"
        # public_key and short_id are empty
        result = _run_redeploy(cluster=cluster)
        build = result["build_xray_config"]
        _, kwargs = build.call_args
        assert "existing_private_key" not in kwargs

    def test_regenerates_when_short_id_missing(self) -> None:
        """If private and public keys present but short_id empty, regenerate."""
        cluster = _configured_cluster(with_keys=False)
        cluster.nodes[0].reality_private_key = "SOME_PRIV"
        cluster.nodes[0].reality_public_key = "SOME_PUB"
        # short_id is empty
        result = _run_redeploy(cluster=cluster)
        build = result["build_xray_config"]
        _, kwargs = build.call_args
        assert "existing_private_key" not in kwargs

    def test_regenerated_keys_saved_to_node(self) -> None:
        """After regeneration, new keys from xray_result are saved to node."""
        cluster = _configured_cluster(with_keys=False)
        result = _run_redeploy(cluster=cluster)
        node = result["cluster"].nodes[0]
        assert node.reality_public_key == "NEW_PUB"
        assert node.reality_short_id == "aabb1122"
        assert node.reality_private_key == "NEW_PRIV"

    def test_preserved_keys_still_updated_from_xray_result(self) -> None:
        """Even when keys are reused, node metadata is set from xray_result."""
        result = _run_redeploy()
        node = result["cluster"].nodes[0]
        # xray_result returns NEW_PUB etc. — those are written to node
        assert node.reality_public_key == "NEW_PUB"
        assert node.reality_private_key == "NEW_PRIV"
        assert node.reality_short_id == "aabb1122"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class TestSetupRedeployFailures:
    def test_fails_when_no_api_token(self) -> None:
        cluster = _configured_cluster()
        cluster.panel.api_token = ""
        with pytest.raises(typer.Exit):
            _run_redeploy(cluster=cluster)

    def test_fails_when_node_not_in_cluster(self) -> None:
        cluster = _configured_cluster()
        resolved = _make_resolved(ip="198.51.100.99")
        with pytest.raises(typer.Exit):
            _run_redeploy(cluster=cluster, resolved=resolved)

    def test_fails_when_panel_unreachable(self) -> None:
        panel = _make_panel_mock()
        panel.ping.return_value = False
        with pytest.raises(typer.Exit):
            _run_redeploy(panel_mock=panel)

    def test_node_container_failure_stops_redeploy_before_metadata_save(self) -> None:
        cluster = _configured_cluster()

        with (
            patch("meridian.panel_bootstrap.MeridianPanel", return_value=_make_panel_mock()),
            patch("meridian.panel_bootstrap.build_xray_config", return_value=_XRAY_RESULT),
            patch("meridian.panel_bootstrap.deploy_node_container", return_value=False),
            patch("meridian.panel_bootstrap.create_hosts_for_node") as mock_hosts,
            patch("meridian.panel_bootstrap.cache_inbounds"),
            patch.object(cluster, "save") as mock_save,
            patch.object(cluster, "backup") as mock_backup,
            pytest.raises(typer.Exit) as exc_info,
        ):
            from meridian.panel_bootstrap import setup_redeploy

            setup_redeploy(
                resolved=_make_resolved(),
                cluster=cluster,
                domain="",
                sni=_SNI,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                version=_VERSION,
            )

        assert exc_info.value.exit_code == 3
        mock_hosts.assert_not_called()
        mock_backup.assert_not_called()
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Config profile
# ---------------------------------------------------------------------------


class TestSetupRedeployConfigProfile:
    def test_creates_new_profile_when_existing_not_found(self) -> None:
        """When find_config_profile_by_name returns None, create with base name."""
        panel = _make_panel_mock()
        panel.find_config_profile_by_name.return_value = None

        new_profile = MagicMock()
        new_profile.uuid = "new-cp-uuid"
        new_profile.name = "meridian-default"
        panel.create_config_profile.return_value = new_profile

        result = _run_redeploy(panel_mock=panel)
        panel.create_config_profile.assert_called_once()
        call_args = panel.create_config_profile.call_args[0]
        # When not found, called with profile_name (no version suffix)
        assert call_args[0] == "meridian-default"
        assert result["cluster"].config_profile_uuid == "new-cp-uuid"

    def test_reuses_existing_profile(self) -> None:
        """When profile already exists, reuse it without creating."""
        panel = _make_panel_mock()
        existing = MagicMock()
        existing.uuid = "old-cp-uuid"
        existing.name = "meridian-default"
        panel.find_config_profile_by_name.return_value = existing

        result = _run_redeploy(panel_mock=panel)
        panel.create_config_profile.assert_not_called()
        assert result["cluster"].config_profile_uuid == "old-cp-uuid"

    def test_profile_update_failure_warns_not_fails(self) -> None:
        """RemnawaveError during profile update is caught and warned, not fatal."""
        panel = _make_panel_mock()
        panel.find_config_profile_by_name.side_effect = RemnawaveError("500 Internal Server Error")

        # Should not raise — profile errors are caught with warn()
        result = _run_redeploy(panel_mock=panel)
        # Cluster save still happens (rest of function continues)
        result["save"].assert_called_once()


# ---------------------------------------------------------------------------
# Node verification
# ---------------------------------------------------------------------------


class TestSetupRedeployNodeVerification:
    def test_node_still_registered_in_panel(self) -> None:
        """When get_node returns a node, no re-registration happens."""
        panel = _make_panel_mock()
        panel.get_node.return_value = MagicMock(uuid="node-uuid-1")

        _run_redeploy(panel_mock=panel)
        panel.create_node.assert_not_called()

    def test_node_deleted_externally_reregistered(self) -> None:
        """When get_node returns None, create_node is called to re-register."""
        panel = _make_panel_mock()
        panel.get_node.return_value = None

        node_creds = MagicMock()
        node_creds.uuid = "re-registered-uuid"
        node_creds.secret_key = "new-secret"
        panel.create_node.return_value = node_creds

        result = _run_redeploy(panel_mock=panel)
        panel.create_node.assert_called_once()
        assert result["cluster"].nodes[0].uuid == "re-registered-uuid"

    def test_node_no_uuid_skips_verification(self) -> None:
        """When node.uuid is empty, panel verification is skipped entirely."""
        cluster = _configured_cluster()
        cluster.nodes[0].uuid = ""
        panel = _make_panel_mock()

        _run_redeploy(cluster=cluster, panel_mock=panel)
        panel.get_node.assert_not_called()
        panel.create_node.assert_not_called()

    def test_node_no_uuid_skips_container_redeploy(self) -> None:
        """When node.uuid is empty, keygen + container deploy is also skipped."""
        cluster = _configured_cluster()
        cluster.nodes[0].uuid = ""
        panel = _make_panel_mock()

        result = _run_redeploy(cluster=cluster, panel_mock=panel)
        result["deploy_node_container"].assert_not_called()


# ---------------------------------------------------------------------------
# Auth errors
# ---------------------------------------------------------------------------


class TestSetupRedeployAuth:
    def test_auth_error_fails_with_panel_api_error(self) -> None:
        """RemnawaveAuthError on ping is caught by outer except RemnawaveError."""
        panel = _make_panel_mock()
        panel.ping.side_effect = RemnawaveAuthError("401 Unauthorized")

        with pytest.raises(typer.Exit):
            _run_redeploy(panel_mock=panel)

    def test_remnawave_error_during_workflow_exits(self) -> None:
        """Any RemnawaveError that escapes the inner try/except fails the function."""
        panel = _make_panel_mock()
        panel.get_node_secret_key.side_effect = RemnawaveError("keygen endpoint unreachable")

        with pytest.raises(typer.Exit):
            _run_redeploy(panel_mock=panel)
