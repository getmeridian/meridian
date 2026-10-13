"""Tests for current cluster-backed relay behavior."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands._helpers import ReviewedApplyPersistenceError
from meridian.core.errors import LocalStateError
from meridian.core.fleet import FleetTopology, TopologyPanel, TopologyRelay
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.models import ProtocolURL, RelayURLSet
from meridian.remnawave import RemnawaveError


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


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
            patch("meridian.commands.relay.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch(
                "meridian.commands.resolve.resolve_server",
                return_value=SimpleNamespace(ip="198.51.100.20", user="root", port=22, machine_id="", conn=MagicMock()),
            ),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=LocalStateError("review state changed"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_deploy("198.51.100.20", "exit-a", relay_name="relay-a", yes=True)

        assert exc_info.value.exit_code == 2
        registry.add.assert_called_once()

    def _rerun(self, exit_arg: str, intent: SetupIntent) -> tuple[MagicMock, BaseException | None]:
        from meridian.commands.relay import run_deploy

        servers = {
            "srv-exit": SimpleNamespace(id="srv-exit", host="198.51.100.10", name="exit-a"),
            "srv-exit-b": SimpleNamespace(id="srv-exit-b", host="198.51.100.11", name="exit-b"),
            "198.51.100.20": SimpleNamespace(id="srv-relay", host="198.51.100.20", name="relay-a"),
        }
        registry = MagicMock()
        registry.find.side_effect = servers.get
        apply = MagicMock(return_value=SimpleNamespace(all_succeeded=True, changed=True))
        error: BaseException | None = None
        with (
            patch("meridian.commands.relay.load_topology_cluster", return_value=ClusterConfig(topology_intent=intent)),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch(
                "meridian.commands.resolve.resolve_server",
                return_value=SimpleNamespace(ip="198.51.100.20", user="root", port=22, machine_id="", conn=MagicMock()),
            ),
            patch("meridian.setup.runtime.SetupRuntime.apply_intent", apply),
        ):
            try:
                run_deploy("198.51.100.20", exit_arg, relay_name="relay-a", yes=True)
            except typer.Exit as exc:
                error = exc
        return apply, error

    def _with_relay(self) -> SetupIntent:
        from meridian.setup.editor import add_relay_to_intent

        return add_relay_to_intent(
            self._v4_intent(), server_ref="srv-relay", title="relay-a", exit_ref="exit-a", listen_port=443
        )

    def test_rerunning_relay_add_resumes_the_declared_relay(self) -> None:
        intent = self._with_relay()

        apply, error = self._rerun("exit-a", intent)

        assert error is None
        assert apply.call_args.args[0] == intent

    def test_relay_add_refuses_to_repoint_an_existing_relay(self, capsys: pytest.CaptureFixture[str]) -> None:
        intent = self._with_relay()
        payload = intent.model_dump(mode="json")
        second = {**payload["exits"][0], "id": "exit-b", "server_ref": "srv-exit-b"}
        second["paths"] = [{**second["paths"][0], "id": "reality-b"}]
        payload["exits"].append(second)
        intent = SetupIntent.model_validate(payload)

        apply, error = self._rerun("exit-b", intent)

        assert isinstance(error, typer.Exit) and error.exit_code == 2
        apply.assert_not_called()
        assert "relay remove relay-a" in capsys.readouterr().err

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
            patch("meridian.commands.relay.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch(
                "meridian.commands.resolve.resolve_server",
                return_value=SimpleNamespace(ip="198.51.100.20", user="root", port=22, machine_id="", conn=MagicMock()),
            ),
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

    def test_relay_help(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        result = CliRunner().invoke(app, ["relay", "--help"])

        assert result.exit_code == 0
        assert "add" in result.output
        assert "list" in result.output
        assert "remove" in result.output
        # `relay check` is a hidden alias of `meridian check RELAY` for one release.
        assert CliRunner().invoke(app, ["relay", "check", "--help"]).exit_code == 0

    def test_relay_add_help_and_hidden_deploy_alias(self) -> None:
        from typer.testing import CliRunner

        from meridian.cli import app

        for command in ("add", "deploy"):
            result = CliRunner().invoke(app, ["relay", command, "--help"])

            assert result.exit_code == 0
            assert "SERVER" in result.output
            assert "Exit server" in result.output


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


class TestRelayListJsonEnvelope:
    def test_empty_list_json_outputs_typed_envelope(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode

        set_json_mode(True)
        try:
            cluster = ClusterConfig(
                panel=PanelConfig(url="https://198.51.100.10/panel", api_token="token"),
                topology_intent=_intent(),
            )
            topology = FleetTopology(
                panel=TopologyPanel(
                    url=cluster.panel.url,
                    display_url=cluster.panel.display_url,
                    server_ip="198.51.100.10",
                    ssh_user="root",
                    ssh_port=22,
                    deployed_with="",
                )
            )
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands.relay.topology_from_local_cluster", return_value=topology),
            ):
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
        from meridian.commands.relay import run_list
        from meridian.console import set_json_mode

        cluster = ClusterConfig(
            panel=PanelConfig(
                url="https://198.51.100.10/panel",
                api_token="tok",
                server_ip="198.51.100.10",
                secret_path="/secret",
            ),
            topology_intent=_intent(),
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
            relays=[
                TopologyRelay(
                    ip="203.0.113.10",
                    name="relay-a",
                    port=443,
                    ssh_user="root",
                    ssh_port=22,
                    exit_node_ip="198.51.100.10",
                    sni="example.com",
                )
            ],
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
                patch("meridian.commands.relay.topology_from_local_cluster", return_value=topology),
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
            ),
            topology_intent=_intent(),
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

        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.10/panel", api_token="tok"),
            topology_intent=_intent(),
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
