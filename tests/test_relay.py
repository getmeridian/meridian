"""Tests for current cluster-backed relay behavior."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, InboundRef, NodeEntry, PanelConfig, ProtocolKey, RelayEntry
from meridian.commands._helpers import ReviewedApplyPersistenceError
from meridian.core.errors import LocalStateError
from meridian.core.fleet import FleetTopology, TopologyPanel, TopologyRelay
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.models import ProtocolURL, RelayURLSet
from meridian.remnawave import RemnawaveError


class TestRelayURLSet:
    def test_frozen(self) -> None:
        url_set = RelayURLSet(
            relay_ip="203.0.113.10",
            relay_name="relay-a",
            urls=[
                ProtocolURL(
                    key="reality",
                    label="Primary",
                    url="vless://test@203.0.113.10:443",
                )
            ],
        )

        assert url_set.relay_ip == "203.0.113.10"
        assert url_set.relay_name == "relay-a"
        assert len(url_set.urls) == 1


class TestRelayCLI:
    @staticmethod
    def _v4_intent() -> SetupIntent:
        return SetupIntent(
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
            access=AccessIntent(users=["default"]),
        )

    def test_v4_deploy_state_error_is_rendered_at_command_boundary(self) -> None:
        from meridian.commands.relay import run_deploy

        cluster = ClusterConfig(topology_intent=self._v4_intent())
        registry = MagicMock()
        registry.find.side_effect = [
            SimpleNamespace(id="srv-exit", host="198.51.100.10", name="exit-a"),
            SimpleNamespace(id="srv-relay", host="198.51.100.20", name="relay-a"),
        ]

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.relay.ServerConnection"),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=LocalStateError("review state changed"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_deploy("198.51.100.20", "exit-a", relay_name="relay-a", yes=True)

        assert exc_info.value.exit_code == 2
        registry.add.assert_called_once()

    def test_v4_deploy_persistence_error_warns_remote_state_may_have_changed(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_deploy

        cluster = ClusterConfig(topology_intent=self._v4_intent())
        registry = MagicMock()
        registry.find.side_effect = [
            SimpleNamespace(id="srv-exit", host="198.51.100.10", name="exit-a"),
            SimpleNamespace(id="srv-relay", host="198.51.100.20", name="relay-a"),
        ]

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.relay.ServerConnection"),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=ReviewedApplyPersistenceError(
                    "read-only filesystem. Remote state may have changed. Repair local state, then rerun "
                    "`meridian plan` and `meridian apply` to reconcile."
                ),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_deploy("198.51.100.20", "exit-a", relay_name="relay-a", yes=True)

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "Remote state may have changed" in output
        assert "meridian plan" in output
        assert "meridian apply" in output

    def test_legacy_deploy_does_not_stop_existing_realm_before_confirmation(self) -> None:
        from meridian.commands.relay import run_deploy

        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token"),
            nodes=[NodeEntry(ip="198.51.100.10", name="exit-a")],
        )
        connection = MagicMock()
        connection.run.side_effect = [
            SimpleNamespace(
                returncode=0,
                stdout='State Recv-Q Send-Q Local Address:Port\nLISTEN 0 128 *:443 users:(("realm",pid=42))\n',
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.commands.relay.confirm", return_value=False),
            patch("meridian.commands.relay.stop_relay_service") as stop_service,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_deploy("198.51.100.20", "exit-a", sni="www.example.com")

        assert exc_info.value.exit_code == 1
        stop_service.assert_not_called()

    def test_deploy_reports_remote_change_when_local_state_cannot_be_saved(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_deploy

        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token"),
            nodes=[NodeEntry(ip="198.51.100.10", name="exit-a", sni="www.example.com")],
            desired_relays=[],
        )
        connection = MagicMock()
        connection.run.side_effect = [
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        provisioner = MagicMock()
        provisioner.run.return_value = []
        panel = MagicMock()
        panel.__enter__.return_value = panel

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.provision.steps.Provisioner", return_value=provisioner),
            patch(
                "meridian.commands.relay.create_relay_hosts",
                return_value={"reality": "550e8400-e29b-41d4-a716-446655440001"},
            ),
            patch("meridian.commands.relay.make_panel", return_value=panel),
            patch("meridian.node_deploy.enforce_host_ordering"),
            patch.object(cluster, "backup"),
            patch.object(cluster, "save", side_effect=[None, OSError("disk full")]) as save,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_deploy(
                "198.51.100.20",
                "exit-a",
                relay_name="relay-a",
                sni="www.example.com",
                yes=True,
            )

        assert exc_info.value.exit_code == 3
        provisioner.run.assert_called_once()
        assert save.call_count == 2
        output = capsys.readouterr().err
        assert "deployed remotely" in output
        assert "Remote state changed" in output

    @staticmethod
    def _projected_relay() -> FleetTopology:
        return FleetTopology(
            panel=TopologyPanel(
                url="https://198.51.100.1/panel",
                display_url="https://198.51.100.1/panel",
                server_ip="198.51.100.1",
                ssh_user="root",
                ssh_port=22,
                deployed_with="v4",
            ),
            relays=[
                TopologyRelay(
                    ip="198.51.100.20",
                    name="relay-a",
                    port=443,
                    ssh_user="root",
                    ssh_port=22,
                    exit_node_ip="198.51.100.10",
                    sni="www.example.com",
                )
            ],
        )

    def test_relay_help(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        result = CliRunner().invoke(app, ["relay", "--help"])

        assert result.exit_code == 0
        assert "deploy" in result.output
        assert "list" in result.output
        assert "remove" in result.output
        assert "check" in result.output

    def test_relay_deploy_help(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        result = CliRunner().invoke(app, ["relay", "deploy", "--help"])

        assert result.exit_code == 0
        assert "RELAY_IP" in result.output
        assert "Exit server" in result.output

    def test_remove_failure_retains_relay_for_retry(self) -> None:
        from meridian.commands.relay import run_remove

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(relays=[relay])
        connection = MagicMock()

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.commands.relay.stop_relay_service", return_value=False),
            pytest.raises(typer.Exit),
        ):
            run_remove(relay.ip, yes=True)

        assert cluster.relays == [relay]

    def test_remove_artifact_failure_retains_relay_for_retry(self) -> None:
        from meridian.commands.relay import run_remove

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(relays=[relay])
        connection = MagicMock()

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.commands.relay.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=False),
            pytest.raises(typer.Exit),
        ):
            run_remove(relay.ip, yes=True)

        assert cluster.relays == [relay]

    def test_remove_reports_remote_change_when_local_state_cannot_be_saved(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_remove

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token"),
            relays=[relay],
        )
        connection = MagicMock()
        panel = MagicMock()
        panel.__enter__.return_value = panel

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.commands.relay.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.commands.relay.delete_relay_hosts", return_value=True),
            patch("meridian.commands.relay.make_panel", return_value=panel),
            patch.object(cluster, "backup"),
            patch.object(cluster, "save", side_effect=OSError("read-only filesystem")),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove(relay.ip, yes=True)

        assert exc_info.value.exit_code == 3
        connection.check_ssh.assert_called_once()
        output = capsys.readouterr().err
        assert "removed remotely" in output
        assert "read-only filesystem" in output

    def test_check_returns_finding_exit_when_service_is_inactive(self) -> None:
        from meridian.commands.relay import run_check

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(relays=[relay])
        connection = MagicMock()
        connection.run.side_effect = [
            SimpleNamespace(returncode=3, stdout="inactive\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        panel = MagicMock()
        panel.__enter__.return_value.list_hosts.return_value = []

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.health.tcp_connect", return_value=True),
            patch("meridian.commands.relay.make_panel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_check(relay.ip)

        assert exc_info.value.exit_code == 4

    def test_check_returns_inconclusive_exit_when_ssh_is_unavailable(self) -> None:
        from meridian.commands.relay import run_check
        from meridian.ssh import SSHError

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(relays=[relay])
        connection = MagicMock()
        connection.check_ssh.side_effect = SSHError("connection refused")

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_check(relay.ip)

        assert exc_info.value.exit_code == 3

    @pytest.mark.parametrize(
        ("status_code", "connectivity_code"),
        [(124, 0), (255, 0), (127, 0), (0, 124), (0, 255), (0, 127)],
    )
    def test_check_returns_inconclusive_when_relay_tools_are_unavailable(
        self,
        status_code: int,
        connectivity_code: int,
    ) -> None:
        from meridian.commands.relay import run_check

        host_uuid = "550e8400-e29b-41d4-a716-446655440001"
        relay = RelayEntry(
            ip="198.51.100.20",
            exit_node_ip="198.51.100.1",
            name="relay-a",
            host_uuids={ProtocolKey.REALITY: host_uuid},
        )
        cluster = ClusterConfig(relays=[relay])
        connection = MagicMock()
        connection.run.side_effect = [
            SimpleNamespace(
                returncode=status_code,
                stdout="active\n" if status_code == 0 else "",
                stderr="",
            ),
            SimpleNamespace(returncode=connectivity_code, stdout="", stderr=""),
        ]
        panel = MagicMock()
        panel.__enter__.return_value.list_hosts.return_value = [
            SimpleNamespace(uuid=host_uuid, is_disabled=False),
        ]

        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection", return_value=connection),
            patch("meridian.health.tcp_connect", return_value=True),
            patch("meridian.commands.relay.make_panel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_check(relay.ip)

        assert exc_info.value.exit_code == 3

    def test_check_rejects_v4_chain_before_legacy_service_checks(self) -> None:
        from meridian.commands.relay import run_check

        cluster = ClusterConfig(topology_intent=self._v4_intent())
        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.topology_from_local_cluster", return_value=self._projected_relay()),
            patch("meridian.commands.relay.ServerConnection") as connection,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_check("198.51.100.20")

        assert exc_info.value.exit_code == 2
        connection.assert_not_called()

    def test_remove_rejects_v4_chain_before_legacy_cleanup(self) -> None:
        from meridian.commands.relay import run_remove

        cluster = ClusterConfig(topology_intent=self._v4_intent())
        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.topology_from_local_cluster", return_value=self._projected_relay()),
            patch("meridian.commands.relay.ServerConnection") as connection,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove("198.51.100.20", yes=True)

        assert exc_info.value.exit_code == 2
        connection.assert_not_called()

    def test_check_validates_requested_exit_before_connecting(self) -> None:
        from meridian.commands.relay import run_check

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="relay-a")
        cluster = ClusterConfig(
            nodes=[
                NodeEntry(ip="198.51.100.1", name="exit-a"),
                NodeEntry(ip="198.51.100.2", name="exit-b"),
            ],
            relays=[relay],
        )
        with (
            patch("meridian.commands.relay.load_cluster", return_value=cluster),
            patch("meridian.commands.relay.ServerConnection") as connection,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_check(relay.ip, exit_arg="exit-b")

        assert exc_info.value.exit_code == 2
        connection.assert_not_called()


class TestNginxStreamRelay:
    def test_stream_config_includes_relay_maps(self) -> None:
        from meridian.provision.nginx_render import render_nginx_stream_config

        config = render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.10",
        )

        assert "include /etc/nginx/stream.d/relay-maps/*.conf;" in config


class TestRelayHelpers:
    def test_relay_hosts_only_advertise_supported_reality_route(self) -> None:
        from meridian.relay_ops import create_relay_hosts

        cluster = ClusterConfig(
            inbounds={
                ProtocolKey.REALITY: InboundRef(
                    uuid="550e8400-e29b-41d4-a716-446655440001",
                    tag="vless-reality",
                ),
                ProtocolKey.XHTTP: InboundRef(
                    uuid="550e8400-e29b-41d4-a716-446655440002",
                    tag="vless-xhttp",
                ),
            },
            config_profile_uuid="550e8400-e29b-41d4-a716-446655440003",
        )
        panel = MagicMock()
        old_xhttp = SimpleNamespace(uuid="550e8400-e29b-41d4-a716-446655440004")
        panel.find_host_by_remark.side_effect = lambda remark: old_xhttp if remark.endswith("-xhttp") else None
        panel.create_host.return_value = SimpleNamespace(uuid="550e8400-e29b-41d4-a716-446655440005")

        result = create_relay_hosts(
            panel,
            cluster,
            "198.51.100.20",
            443,
            "relay.test",
            "relay-a",
        )

        assert result == {"reality": "550e8400-e29b-41d4-a716-446655440005"}
        panel.create_host.assert_called_once()
        assert panel.create_host.call_args.kwargs["inbound_uuid"] == "550e8400-e29b-41d4-a716-446655440001"
        panel.delete_host.assert_called_once_with(old_xhttp.uuid)

    def test_relay_host_deploy_fails_when_deprecated_host_cannot_be_removed(self) -> None:
        from meridian.relay_ops import create_relay_hosts

        cluster = ClusterConfig(
            inbounds={
                ProtocolKey.REALITY: InboundRef(
                    uuid="550e8400-e29b-41d4-a716-446655440001",
                    tag="vless-reality",
                )
            },
            config_profile_uuid="550e8400-e29b-41d4-a716-446655440003",
        )
        panel = MagicMock()
        panel.find_host_by_remark.return_value = SimpleNamespace(uuid="550e8400-e29b-41d4-a716-446655440004")
        panel.delete_host.side_effect = RemnawaveError("panel unavailable")

        with pytest.raises(RemnawaveError, match="deprecated relay XHTTP host"):
            create_relay_hosts(panel, cluster, "198.51.100.20", 443, "relay.test", "relay-a")

    def test_relay_label_from_name(self) -> None:
        from meridian.relay_ops import relay_label

        assert relay_label(RelayEntry(ip="203.0.113.10", name="relay-a")) == "relay-a"

    def test_relay_label_from_ip(self) -> None:
        from meridian.relay_ops import relay_label

        assert relay_label(RelayEntry(ip="203.0.113.10")) == "203-0-113-10"

    def test_relay_xray_port_is_stable_and_host_specific(self) -> None:
        from meridian.relay_ops import relay_xray_port

        port = relay_xray_port("203.0.113.10")
        assert 40000 <= port <= 49999
        assert relay_xray_port("203.0.113.10") == port
        assert relay_xray_port("203.0.113.10") != relay_xray_port("203.0.113.11")

    def test_delete_hosts_treats_missing_as_idempotent_success(self) -> None:
        from meridian.relay_ops import delete_relay_hosts
        from meridian.remnawave import RemnawaveNotFoundError

        panel = MagicMock()
        panel.delete_host.side_effect = RemnawaveNotFoundError("already absent")
        relay = RelayEntry(
            ip="198.51.100.20",
            host_uuids={"reality": "550e8400-e29b-41d4-a716-446655440001"},
        )

        assert delete_relay_hosts(panel, relay) is True

    def test_remove_nginx_reports_config_file_delete_failure(self) -> None:
        from meridian.relay_ops import remove_relay_nginx

        connection = MagicMock()
        connection.run.return_value = SimpleNamespace(returncode=1)

        assert remove_relay_nginx(connection, RelayEntry(ip="198.51.100.20")) is False
        assert connection.run.call_count == 1

    def test_stop_service_treats_missing_systemd_unit_as_clean(self) -> None:
        from meridian.relay_ops import stop_relay_service

        connection = MagicMock()
        connection.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

        assert stop_relay_service(connection) is True

        command = connection.run.call_args.args[0]
        assert "LoadState" in command
        assert '"not-found"' in command
        assert "systemctl stop meridian-relay" in command
        assert "systemctl disable meridian-relay" in command

    def test_stop_service_propagates_real_systemd_failure(self) -> None:
        from meridian.relay_ops import stop_relay_service

        connection = MagicMock()
        connection.run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

        assert stop_relay_service(connection) is False


class TestRelayProvisioningBranches:
    def test_install_realm_rejects_checksum_mismatch(self) -> None:
        from meridian.provision.relay import InstallRealm, RelayContext

        conn = MagicMock()

        def run(command: str, **_kwargs: object) -> MagicMock:
            result = MagicMock(returncode=0, stdout="", stderr="")
            if "realm --version" in command:
                result.returncode = 1
            elif "uname -m" in command:
                result.stdout = "x86_64\n"
            elif "sha256sum" in command:
                result.stdout = f"{'0' * 64}\n"
            return result

        conn.run.side_effect = run
        result = InstallRealm().run(
            conn,
            RelayContext(relay_ip="203.0.113.10", exit_ip="198.51.100.10"),
        )

        assert result.status == "failed"
        assert "checksum mismatch" in result.detail

    def test_verify_relay_retries_until_service_is_active(self) -> None:
        from meridian.provision.relay import RelayContext, VerifyRelay

        conn = MagicMock()
        conn.run.side_effect = [
            MagicMock(returncode=3, stdout="inactive\n", stderr=""),
            MagicMock(returncode=3, stdout="inactive\n", stderr=""),
            MagicMock(returncode=0, stdout="active\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with patch("meridian.provision.relay.time.sleep") as sleep:
            result = VerifyRelay().run(
                conn,
                RelayContext(relay_ip="203.0.113.10", exit_ip="198.51.100.10"),
            )

        assert result.status == "ok"
        assert sleep.call_count == 2


class TestRelayClusterSerialization:
    def test_save_load_roundtrip_preserves_current_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yml"
        cluster = ClusterConfig(
            relays=[
                RelayEntry(
                    ip="203.0.113.10",
                    name="relay-a",
                    port=9443,
                    exit_node_ip="198.51.100.10",
                    sni="example.com",
                    ssh_user="ubuntu",
                    ssh_port=2222,
                )
            ]
        )

        cluster.save(path)
        loaded = ClusterConfig.load(path)

        assert loaded.relays == cluster.relays


class TestRelayListJsonEnvelope:
    def test_empty_list_json_outputs_typed_envelope(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode

        set_json_mode(True)
        try:
            cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/panel", api_token="token"))
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "relay.list"
        assert payload["summary"]["counts"] == {"relays": 0}
        assert payload["data"] == {"relays": []}

    def test_list_json_outputs_envelope(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.cluster import InboundRef, NodeEntry, PanelConfig, ProtocolKey
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode
        from meridian.remnawave import RemnawaveError

        cluster = ClusterConfig(
            panel=PanelConfig(
                url="https://198.51.100.10/panel",
                api_token="tok",
                server_ip="198.51.100.10",
                secret_path="/secret",
            ),
            nodes=[
                NodeEntry(
                    ip="198.51.100.10",
                    uuid="550e8400-e29b-41d4-a716-446655440001",
                    is_panel_host=True,
                    name="panel-node",
                )
            ],
            relays=[
                RelayEntry(
                    ip="203.0.113.10",
                    name="relay-a",
                    exit_node_ip="198.51.100.10",
                    port=443,
                    sni="example.com",
                )
            ],
            inbounds={
                ProtocolKey.REALITY: InboundRef(
                    uuid="550e8400-e29b-41d4-a716-446655440010",
                    tag="vless-reality",
                ),
            },
        )
        panel = MagicMock()
        panel.__enter__.return_value = panel
        panel.__exit__.return_value = False
        panel.list_hosts.side_effect = RemnawaveError("Panel unreachable")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert exc_info.value.exit_code == 3
        assert payload["exit_code"] == 3
        assert payload["warnings"][0]["code"] == "MERIDIAN_RELAY_HOST_STATUS_UNAVAILABLE"
        assert payload["command"] == "relay.list"
        assert payload["status"] == "ok"
        assert payload["summary"]["counts"]["relays"] == 1
        assert payload["data"]["relays"][0]["ip"] == "203.0.113.10"
        assert payload["data"]["relays"][0]["name"] == "relay-a"
        assert payload["data"]["relays"][0]["sni"] == "example.com"

    def test_list_uses_v4_topology_projection(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.cluster import PanelConfig
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode
        from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel, TopologyRelay

        cluster = ClusterConfig(
            panel=PanelConfig(
                url="https://198.51.100.10/panel",
                api_token="tok",
            )
        )
        topology = FleetTopology(
            panel=TopologyPanel(
                url=cluster.panel.url,
                display_url=cluster.panel.display_url,
                server_ip="198.51.100.10",
                ssh_user="root",
                ssh_port=22,
                deployed_with="",
            ),
            nodes=[
                TopologyNode(
                    ip="198.51.100.20",
                    name="exit-v4",
                    uuid="node-v4",
                    is_panel_host=False,
                    ssh_user="root",
                    ssh_port=22,
                    domain="",
                    sni="www.example.com",
                    xhttp_path="",
                    ws_path="",
                )
            ],
            relays=[
                TopologyRelay(
                    ip="198.51.100.30",
                    name="relay-v4",
                    port=8443,
                    ssh_user="root",
                    ssh_port=22,
                    exit_node_ip="198.51.100.20",
                    sni="www.example.com",
                )
            ],
        )
        panel = MagicMock()
        panel.list_hosts.return_value = []

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.relay.topology_from_local_cluster", return_value=topology),
            ):
                run_list(exit_arg="exit-v4")
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["counts"]["relays"] == 1
        assert payload["data"]["relays"][0]["ip"] == "198.51.100.30"
        assert payload["data"]["relays"][0]["exit_node_ip"] == "198.51.100.20"

    def test_list_keeps_shared_relay_server_status_separate_by_port(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode
        from meridian.core.fleet import RelayHostRef
        from meridian.remnawave import Host

        cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/panel", api_token="tok"))
        topology = FleetTopology(
            panel=TopologyPanel(
                url=cluster.panel.url,
                display_url=cluster.panel.display_url,
                server_ip="198.51.100.10",
                ssh_user="root",
                ssh_port=22,
                deployed_with="",
            ),
            relays=[
                TopologyRelay(
                    ip="198.51.100.30",
                    name="chain-a",
                    port=8443,
                    ssh_user="root",
                    ssh_port=22,
                    exit_node_ip="198.51.100.20",
                    sni="www.example.com",
                    host_refs=[RelayHostRef(protocol="reality", uuid="host-a")],
                ),
                TopologyRelay(
                    ip="198.51.100.30",
                    name="chain-b",
                    port=9443,
                    ssh_user="root",
                    ssh_port=22,
                    exit_node_ip="198.51.100.21",
                    sni="www.example.com",
                    host_refs=[RelayHostRef(protocol="reality", uuid="host-b")],
                ),
            ],
        )
        panel = MagicMock()
        panel.__enter__.return_value = panel
        panel.__exit__.return_value = False
        panel.list_hosts.return_value = [Host(uuid="host-a"), Host(uuid="host-b", is_disabled=True)]

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.relay.topology_from_local_cluster", return_value=topology),
            ):
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert [relay["enabled"] for relay in payload["data"]["relays"]] == [True, False]
