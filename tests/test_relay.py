"""Tests for current cluster-backed relay behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, RelayEntry
from meridian.models import ProtocolURL, RelayURLSet


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
        panel.list_hosts.side_effect = RemnawaveError("Panel unreachable")

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
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "relay.list"
        assert payload["status"] == "ok"
        assert payload["summary"]["counts"]["relays"] == 1
        assert payload["data"]["relays"][0]["ip"] == "203.0.113.10"
        assert payload["data"]["relays"][0]["name"] == "relay-a"
        assert payload["data"]["relays"][0]["sni"] == "example.com"
