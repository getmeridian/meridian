"""Tests for run() entry point — mode detection, input validation, port computation.

Covers the deploy command's orchestration: first deploy vs redeploy vs blocked,
IP/flag validation, deterministic port computation, and path reuse.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.adapters import RemoteExecutorConnection
from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
    ProtocolKey,
)
from meridian.commands.setup import run
from meridian.core.deploy import DeployRequest, DeployResult
from meridian.core.deploy_planning import DeployClusterState, build_deploy_plan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IP_A = "198.51.100.1"
_IP_B = "198.51.100.2"
_UUID_A = "550e8400-e29b-41d4-a716-446655440000"
_UUID_B = "660e8400-e29b-41d4-a716-446655440001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_cluster() -> ClusterConfig:
    return ClusterConfig()


def _configured_cluster(ip: str = _IP_A) -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(
            url=f"https://{ip}/panel",
            api_token="test-jwt-token",
            server_ip=ip,
            secret_path="existing_secret_path",
        ),
        nodes=[
            NodeEntry(
                ip=ip,
                uuid=_UUID_A,
                is_panel_host=True,
                xhttp_path="existing_xhttp_path",
                ws_path="existing_ws_path",
            ),
        ],
        inbounds={
            ProtocolKey.REALITY: InboundRef(uuid=_UUID_B, tag="vless-reality"),
        },
    )


def _make_resolved(ip: str = _IP_A) -> SimpleNamespace:
    conn = MagicMock()
    conn.ip = ip
    conn.user = "root"
    conn.port = 22
    conn.local_mode = False
    return SimpleNamespace(ip=ip, user="root", conn=conn, local_mode=False)


def _deploy_result(ip: str = _IP_A) -> DeployResult:
    return DeployResult(
        mode="first_deploy",
        server_ip=ip,
        ssh_user="root",
        ssh_port=22,
        domain="vpn.example",
        sni="www.microsoft.com",
        client_name="default",
        harden=True,
        pq=False,
        warp=False,
        geo_block=True,
        panel_url=f"https://{ip}/panel",
        panel_secret_path="secret_path",
        connection_page_path="connection_path",
        node_count=1,
        relay_count=0,
        summary=f"Deploy completed for {ip}",
    )


def _runtime_event_shape(event: dict[str, object]) -> dict[str, object]:
    return {key: event[key] for key in ("type", "phase", "message", "data")}


def _patch_all(**overrides: object):  # noqa: ANN202
    """Context manager that patches all external calls in run().

    Returns a dict of all mocks for assertion.
    """
    defaults = {
        "meridian.commands.setup.resolve_server": MagicMock(return_value=_make_resolved()),
        "meridian.commands.setup.ensure_server_connection": MagicMock(side_effect=lambda r: r),
        "meridian.commands.setup._check_ports": MagicMock(),
        "meridian.commands.setup.run_provisioner": MagicMock(),
        "meridian.commands.setup.configure_panel_and_node": MagicMock(),
        "meridian.commands.setup._print_success": MagicMock(),
        "meridian.commands.setup._offer_relay": MagicMock(),
    }
    defaults.update(overrides)

    import contextlib

    return contextlib.ExitStack(), defaults


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


class TestRunCoreBoundary:
    def test_run_builds_deploy_request_for_core_service(self) -> None:
        with patch("meridian.commands.setup.deploy_server") as mock_deploy:
            run(
                ip=_IP_A,
                domain="vpn.example",
                sni="www.microsoft.com",
                client_name="alice",
                user="admin",
                yes=True,
                harden=False,
                requested_server="",
                server_name="Family VPN",
                icon="shield",
                color="ocean",
                pq=True,
                warp=True,
                geo_block=False,
                ssh_port=2222,
            )

        request = mock_deploy.call_args.args[0]
        assert request.ip == _IP_A
        assert request.domain == "vpn.example"
        assert request.client_name == "alice"
        assert request.user == "admin"
        assert request.yes is True
        assert request.harden is False
        assert request.server_name == "Family VPN"
        assert request.pq is True
        assert request.warp is True
        assert request.geo_block is False
        assert request.ssh_port == 2222
        assert mock_deploy.call_args.kwargs["executor"].__name__ == "_execute_deploy_request"

    def test_run_collects_wizard_input_before_core_service(self) -> None:
        from meridian.commands.wizard import WizardResult

        wizard_result = WizardResult(
            ip=_IP_A,
            user="admin",
            sni="www.microsoft.com",
            domain="vpn.example",
            harden=False,
            client_name="alice",
            server_name="Family VPN",
            icon="shield",
            color="ocean",
            pq=True,
            warp=True,
            geo_block=False,
        )
        with (
            patch("meridian.commands.wizard.interactive_wizard", return_value=wizard_result) as mock_wizard,
            patch("meridian.commands.setup.deploy_server") as mock_deploy,
        ):
            run(yes=True)

        mock_wizard.assert_called_once()
        request = mock_deploy.call_args.args[0]
        assert request.ip == _IP_A
        assert request.user == "admin"
        assert request.domain == "vpn.example"
        assert request.harden is False
        assert request.client_name == "alice"
        assert request.server_name == "Family VPN"
        assert request.pq is True
        assert request.warp is True
        assert request.geo_block is False

    def test_run_emits_deploy_json_envelope(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("meridian.commands.setup.deploy_server", return_value=_deploy_result()) as mock_deploy:
            run(ip=_IP_A, yes=True, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "deploy"
        assert payload["status"] == "changed"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["server_ip"] == _IP_A
        assert mock_deploy.call_args.kwargs["reporter"] is None

    def test_run_loads_deploy_request_file(self, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
        request_path = tmp_path / "deploy.json"
        request = DeployRequest(ip=_IP_A, user="admin", yes=True, domain="vpn.example")
        request_path.write_text(request.model_dump_json(), encoding="utf-8")

        with patch("meridian.commands.setup.deploy_server", return_value=_deploy_result()) as mock_deploy:
            run(request_path=str(request_path), json_output=True)

        loaded_request = mock_deploy.call_args.args[0]
        assert loaded_request.ip == _IP_A
        assert loaded_request.user == "admin"
        assert loaded_request.yes is True
        assert json.loads(capsys.readouterr().out)["command"] == "deploy"

    def test_run_streams_deploy_events_as_jsonl(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("meridian.commands.setup._execute_deploy_request", return_value=_deploy_result()):
            run(ip=_IP_A, yes=True, events="jsonl")

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]

        assert output["command"] == "deploy"
        assert output["status"] == "changed"
        assert [event["type"] for event in events] == ["command.started", "command.completed"]
        assert events[0]["schema"] == "meridian.event/v1"
        assert events[0]["operation_id"] == output["operation_id"]

    def test_run_dry_run_emits_deploy_plan_without_executing(self, capsys: pytest.CaptureFixture[str]) -> None:
        plan = build_deploy_plan(
            _IP_A,
            DeployClusterState(is_configured=False),
            token_hex=lambda n: "0" * (n * 2),
        )
        with (
            patch("meridian.commands.setup.dry_run_deploy_request", return_value=plan) as mock_dry_run,
            patch("meridian.commands.setup.deploy_server") as mock_deploy,
            patch("meridian.commands.setup.resolve_server") as mock_resolve,
            patch("meridian.commands.setup.ensure_server_connection") as mock_ensure,
            patch("meridian.commands.setup._check_ports") as mock_check_ports,
        ):
            run(ip=_IP_A, dry_run=True, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "deploy"
        assert payload["status"] == "ok"
        assert payload["data"]["mode"] == "first_deploy"
        assert payload["data"]["server_ip"] == _IP_A
        mock_dry_run.assert_called_once()
        mock_deploy.assert_not_called()
        mock_resolve.assert_not_called()
        mock_ensure.assert_not_called()
        mock_check_ports.assert_not_called()

    def test_run_dry_run_request_file_does_not_require_confirmation(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        request_path = tmp_path / "deploy.json"
        request = DeployRequest(ip=_IP_A, yes=False)
        request_path.write_text(request.model_dump_json(), encoding="utf-8")

        with (
            patch("meridian.commands.setup.ClusterConfig.load", return_value=_empty_cluster()),
            patch("meridian.commands.setup.deploy_server") as mock_deploy,
        ):
            run(request_path=str(request_path), dry_run=True, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["data"]["mode"] == "first_deploy"
        mock_deploy.assert_not_called()

    def test_run_dry_run_events_jsonl_emits_plan_events(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("meridian.commands.setup.ClusterConfig.load", return_value=_empty_cluster()):
            run(ip="198.51.100.10", dry_run=True, events="jsonl")

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
        fixture_events = [
            json.loads(line)
            for line in (
                Path(__file__).resolve().parents[1]
                / "contracts"
                / "meridian"
                / "v1"
                / "fixtures"
                / "deploy-dry-run-events.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]

        assert output["command"] == "deploy"
        assert output["status"] == "ok"
        assert [event["type"] for event in events] == ["command.started", "command.completed"]
        assert {event["operation_id"] for event in events} == {output["operation_id"]}
        assert [event["data"]["dry_run"] for event in events] == [True, True]
        assert [_runtime_event_shape(event) for event in events] == [
            _runtime_event_shape(event) for event in fixture_events
        ]

    def test_run_dry_run_redeploy_reuses_existing_paths(self, capsys: pytest.CaptureFixture[str]) -> None:
        cluster = _configured_cluster(_IP_A)
        cluster.panel.sub_path = "existing_info_page"
        with patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster):
            run(ip=_IP_A, dry_run=True, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["mode"] == "redeploy"
        assert payload["data"]["secret_path"] == "[redacted]"
        assert payload["data"]["xhttp_path"] == "existing_xhttp_path"
        assert payload["data"]["ws_path"] == "existing_ws_path"
        assert payload["data"]["info_page_path"] == "existing_info_page"

    def test_run_dry_run_new_ip_on_configured_cluster_fails_with_node_add_hint(self) -> None:
        with (
            patch("meridian.commands.setup.ClusterConfig.load", return_value=_configured_cluster(_IP_A)),
            patch("meridian.commands.setup.deploy_server") as mock_deploy,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run(ip=_IP_B, dry_run=True, json_output=True)

        assert exc_info.value.exit_code == 2
        mock_deploy.assert_not_called()

    def test_run_dry_run_resolves_registered_server_without_ssh(self, capsys: pytest.CaptureFixture[str]) -> None:
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(host=_IP_B, user="admin")
        with (
            patch("meridian.commands.setup.ServerRegistry", return_value=registry),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=_empty_cluster()),
            patch("meridian.commands.setup.resolve_server") as mock_resolve,
        ):
            run(requested_server="edge", dry_run=True, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["server_ip"] == _IP_B
        mock_resolve.assert_not_called()

    def test_run_uses_registered_server_ssh_port_for_execution(self) -> None:
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(host=_IP_B, user="admin", port=2222)
        resolved = _make_resolved(_IP_B)
        resolved.user = "admin"
        resolved.conn.user = "admin"
        resolved.conn.port = 2222

        with (
            patch("meridian.commands.setup.ServerRegistry", return_value=registry),
            patch("meridian.commands.setup.resolve_server", return_value=resolved) as mock_resolve,
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=_empty_cluster()),
            patch("meridian.commands.setup.run_provisioner"),
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(requested_server="edge", yes=True)

        assert mock_resolve.call_args.kwargs["explicit_ip"] == _IP_B
        assert mock_resolve.call_args.kwargs["user"] == "admin"
        assert mock_resolve.call_args.kwargs["port"] == 2222

    def test_run_machine_mode_requires_confirmation(self) -> None:
        with (
            patch("meridian.commands.setup.deploy_server") as mock_deploy,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run(ip=_IP_A, json_output=True)

        assert exc_info.value.exit_code == 2
        mock_deploy.assert_not_called()


class TestRunModeDetection:
    def _run_with_cluster(self, cluster: ClusterConfig, ip: str = _IP_A) -> dict:
        """Execute run() with given cluster config, capturing calls."""
        resolved = _make_resolved(ip)
        mocks: dict[str, MagicMock] = {}
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node") as mock_config,
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            mocks["_run_provisioner"] = mock_prov
            mocks["_configure_panel_and_node"] = mock_config
            run(ip=ip, yes=True)
        return mocks

    def test_empty_cluster_triggers_first_deploy(self) -> None:
        mocks = self._run_with_cluster(_empty_cluster())
        call_kwargs = mocks["_configure_panel_and_node"].call_args
        assert call_kwargs.kwargs["is_first_deploy"] is True
        assert call_kwargs.kwargs["is_redeploy"] is False

    def test_existing_node_ip_triggers_redeploy(self) -> None:
        mocks = self._run_with_cluster(_configured_cluster(_IP_A), ip=_IP_A)
        call_kwargs = mocks["_configure_panel_and_node"].call_args
        assert call_kwargs.kwargs["is_first_deploy"] is False
        assert call_kwargs.kwargs["is_redeploy"] is True

    def test_new_ip_on_configured_cluster_fails_with_node_add_hint(self) -> None:
        cluster = _configured_cluster(_IP_A)
        resolved = _make_resolved(_IP_B)
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run(ip=_IP_B, yes=True)
        assert exc_info.value.exit_code != 0


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestRunInputValidation:
    def test_invalid_ip_exits(self) -> None:
        with pytest.raises(typer.Exit):
            run(ip="not-an-ip", yes=True)

    def test_both_ip_and_server_flag_fails(self) -> None:
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            pytest.raises(typer.Exit),
        ):
            run(ip=_IP_A, requested_server="mybox", yes=True)

    def test_invalid_client_name_exits(self) -> None:
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            pytest.raises(typer.Exit),
        ):
            run(ip=_IP_A, client_name="invalid--!name", yes=True)

    def test_empty_client_name_defaults_to_default(self) -> None:
        cluster = _empty_cluster()
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner"),
            patch("meridian.commands.setup.configure_panel_and_node") as mock_config,
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, client_name="", yes=True)
        assert mock_config.call_args.kwargs["client_name"] == "default"

    def test_valid_client_name_passes_through(self) -> None:
        cluster = _empty_cluster()
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner"),
            patch("meridian.commands.setup.configure_panel_and_node") as mock_config,
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, client_name="alice", yes=True)
        assert mock_config.call_args.kwargs["client_name"] == "alice"


# ---------------------------------------------------------------------------
# Port computation
# ---------------------------------------------------------------------------


class TestRunPortComputation:
    def _get_ports(self, ip: str) -> dict:
        """Run deploy and capture the port values passed to provisioner."""
        cluster = _empty_cluster()
        resolved = _make_resolved(ip)
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=ip, yes=True)
        return {
            "xhttp_port": mock_prov.call_args.kwargs["xhttp_port"],
            "reality_port": mock_prov.call_args.kwargs["reality_port"],
            "wss_port": mock_prov.call_args.kwargs["wss_port"],
        }

    def test_ports_deterministic_from_ip(self) -> None:
        ports1 = self._get_ports(_IP_A)
        ports2 = self._get_ports(_IP_A)
        assert ports1 == ports2

    def test_different_ips_produce_different_ports(self) -> None:
        ports_a = self._get_ports(_IP_A)
        ports_b = self._get_ports(_IP_B)
        # At least one port differs
        assert ports_a != ports_b

    def test_xhttp_port_in_range(self) -> None:
        ports = self._get_ports(_IP_A)
        assert 30000 <= ports["xhttp_port"] < 40000

    def test_reality_port_in_range(self) -> None:
        ports = self._get_ports(_IP_A)
        assert 10000 <= ports["reality_port"] < 11000

    def test_wss_port_in_range(self) -> None:
        ports = self._get_ports(_IP_A)
        assert 20000 <= ports["wss_port"] < 30000

    def test_ports_match_hash_formula(self) -> None:
        ip_hash = int(hashlib.sha256(_IP_A.encode()).hexdigest()[:8], 16)
        ports = self._get_ports(_IP_A)
        assert ports["xhttp_port"] == 30000 + (ip_hash % 10000)
        assert ports["reality_port"] == 10000 + ip_hash % 1000
        assert ports["wss_port"] == 20000 + (ip_hash % 10000)


# ---------------------------------------------------------------------------
# Secret path reuse
# ---------------------------------------------------------------------------


class TestRunSecretPathReuse:
    def test_first_deploy_generates_new_secret_path(self) -> None:
        cluster = _empty_cluster()
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True)
        secret_path = mock_prov.call_args.kwargs["secret_path"]
        assert len(secret_path) == 24  # secrets.token_hex(12) → 24 chars
        assert secret_path != ""

    def test_redeploy_reuses_existing_secret_path(self) -> None:
        cluster = _configured_cluster(_IP_A)
        resolved = _make_resolved(_IP_A)
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True)
        assert mock_prov.call_args.kwargs["secret_path"] == "existing_secret_path"


# ---------------------------------------------------------------------------
# XHTTP/WS path reuse
# ---------------------------------------------------------------------------


class TestRunPathReuse:
    def test_first_deploy_generates_new_xhttp_path(self) -> None:
        cluster = _empty_cluster()
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True)
        xhttp_path = mock_prov.call_args.kwargs["xhttp_path"]
        ws_path = mock_prov.call_args.kwargs["ws_path"]
        assert len(xhttp_path) == 16  # secrets.token_hex(8)
        assert len(ws_path) == 16

    def test_redeploy_reuses_existing_paths(self) -> None:
        cluster = _configured_cluster(_IP_A)
        resolved = _make_resolved(_IP_A)
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner") as mock_prov,
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True)
        assert mock_prov.call_args.kwargs["xhttp_path"] == "existing_xhttp_path"
        assert mock_prov.call_args.kwargs["ws_path"] == "existing_ws_path"


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------


class TestRunBranding:
    def test_branding_saved_when_provided(self) -> None:
        cluster = _empty_cluster()
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner"),
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True, server_name="My VPN", icon="shield", color="ocean")
        assert cluster.branding is not None
        assert cluster.branding.server_name == "My VPN"
        assert cluster.branding.icon == "shield"
        assert cluster.branding.color == "ocean"

    def test_branding_not_overwritten_when_all_empty(self) -> None:
        """When no branding flags given, existing branding stays unchanged."""
        cluster = _empty_cluster()
        original_branding = cluster.branding
        resolved = _make_resolved()
        with (
            patch("meridian.commands.setup.ServerRegistry"),
            patch("meridian.commands.setup.resolve_server", return_value=resolved),
            patch("meridian.commands.setup.ensure_server_connection", side_effect=lambda r: r),
            patch("meridian.commands.setup._check_ports"),
            patch("meridian.commands.setup.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.setup.run_provisioner"),
            patch("meridian.commands.setup.configure_panel_and_node"),
            patch("meridian.commands.setup.ClusterConfig.save"),
            patch("meridian.commands.setup._print_success"),
            patch("meridian.commands.setup._offer_relay"),
            patch("meridian.commands.setup._build_redeploy_command", return_value=""),
        ):
            run(ip=_IP_A, yes=True)
        # Branding should not be replaced with new values
        assert cluster.branding is original_branding


class TestSubscriptionPagePathPersistence:
    """Regression: `_run_provisioner` must create and persist
    `cluster.subscription_page` with a generated nginx route on the very
    first deploy. If it doesn't, the next `meridian apply` disable-cycle
    skips nginx cleanup (empty path) and a subsequent re-enable adds a
    duplicate route — found by Round 2 dual review on v4-declarative."""

    def test_first_deploy_creates_subscription_page_with_path(self) -> None:
        """After _run_provisioner completes on a cluster with no existing
        subscription_page section, cluster.subscription_page must be a
        non-None SubscriptionPageConfig with a non-empty path."""
        from meridian.cluster import ClusterConfig
        from meridian.panel_bootstrap import run_provisioner

        cluster = ClusterConfig()
        assert cluster.subscription_page is None
        resolved = _make_resolved()

        with (
            patch("meridian.provision.Provisioner.run", return_value=[]) as mock_run,
            patch("meridian.panel_bootstrap.ok"),
            patch("meridian.panel_bootstrap.info"),
            patch("meridian.panel_bootstrap.fail"),
        ):
            run_provisioner(
                resolved=resolved,  # type: ignore[arg-type]
                cluster=cluster,
                domain="",
                sni="www.microsoft.com",
                harden=False,
                is_panel_host=True,
                secret_path="secret",
                xhttp_port=30000,
                reality_port=10000,
                wss_port=20000,
            )

        assert cluster.subscription_page is not None, (
            "subscription_page must be created during first deploy — "
            "otherwise a later REMOVE_SUBSCRIPTION_PAGE would skip nginx "
            "cleanup and re-enable would create duplicate routes"
        )
        assert cluster.subscription_page.path, (
            "subscription_page.path must be non-empty after first deploy "
            "so nginx cleanup can find and remove the route on disable"
        )
        # Path is secrets.token_hex(8) — 16 hex chars
        assert len(cluster.subscription_page.path) == 16
        assert isinstance(mock_run.call_args.args[0], RemoteExecutorConnection)

    def test_redeploy_reuses_existing_subscription_page_path(self) -> None:
        """When cluster.yml already has a subscription_page.path, re-running
        _run_provisioner must NOT rotate it — otherwise nginx would end up
        with both the old and new paths until the old one is cleaned up."""
        from meridian.cluster import ClusterConfig, SubscriptionPageConfig
        from meridian.panel_bootstrap import run_provisioner

        cluster = ClusterConfig(subscription_page=SubscriptionPageConfig(path="stable_path_abc"))
        resolved = _make_resolved()

        with (
            patch("meridian.provision.Provisioner.run", return_value=[]),
            patch("meridian.panel_bootstrap.ok"),
            patch("meridian.panel_bootstrap.info"),
            patch("meridian.panel_bootstrap.fail"),
        ):
            run_provisioner(
                resolved=resolved,  # type: ignore[arg-type]
                cluster=cluster,
                domain="",
                sni="www.microsoft.com",
                harden=False,
                is_panel_host=True,
                secret_path="secret",
                xhttp_port=30000,
                reality_port=10000,
                wss_port=20000,
            )

        assert cluster.subscription_page is not None
        assert cluster.subscription_page.path == "stable_path_abc"
