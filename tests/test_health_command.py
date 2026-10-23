"""`meridian check` dispatches by fleet role and reports on the verification contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, SubscriptionPageConfig
from meridian.commands import health
from meridian.core.fleet import TopologyNode
from meridian.core.verification import build_verification_check
from meridian.diagnostics.checks import CheckResult
from meridian.diagnostics.evidence import finding
from meridian.remnawave import RemnawaveError
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import add_relay_to_intent
from meridian.setup.single_server import single_server_intent

PASSED = CheckResult(name="check", status="passed", detail="healthy")
PLUGINS_READY = build_verification_check(
    "node_plugins",
    "Node plugin readiness",
    [finding("NODE_PLUGIN_KERNEL", "passed", "Kernel 6.8.0 supports node plugins")],
)
DNS_REACHABLE = build_verification_check(
    "exit_dns",
    "Encrypted DNS",
    [finding("EXIT_DNS", "passed", "Cloudflare, Quad9 and Google answer DNS over HTTPS")],
)


@pytest.fixture
def fleet(servers_file: Path) -> SimpleNamespace:
    """One control+exit server and one relay into it, saved like a real deploy."""
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="exit-a"))
    registry.add(ServerEntry(host="198.51.100.20", name="relay-a"))
    exit_ref = registry.find("exit-a").id
    relay_ref = registry.find("relay-a").id
    intent = single_server_intent(None, server_ref=exit_ref, sni="www.example.com")
    intent = add_relay_to_intent(intent, server_ref=relay_ref, title="relay-a", exit_ref="main", listen_port=8443)
    cluster = ClusterConfig(topology_intent=intent, subscription_page=SubscriptionPageConfig(deployed=True))
    cluster.save()
    node = TopologyNode(
        ip="198.51.100.10",
        name="exit-a",
        uuid="node-uuid",
        is_panel_host=True,
        ssh_user="root",
        ssh_port=22,
        domain="",
        sni="www.example.com",
        xhttp_path="",
        ws_path="",
    )
    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.get_node.return_value = SimpleNamespace(is_disabled=False, is_connected=True)
    conn = MagicMock(local_mode=False, user="root")
    deployment = SimpleNamespace(
        public_tcp_ports=(443,),
        public_udp_ports=(),
        internal_ports={},
        public_tls_ports=(443,),
        https_routes=(SimpleNamespace(port=443, tls_sni="www.example.com", host_header=""),),
    )
    return SimpleNamespace(cluster=cluster, node=node, panel=panel, conn=conn, deployment=deployment)


def _run(fleet: SimpleNamespace, *args: str, **kwargs: object) -> SimpleNamespace:
    mocks = SimpleNamespace(
        containers=MagicMock(return_value=PASSED),
        ports=MagicMock(return_value=PASSED),
        tls=MagicMock(return_value=PASSED),
        blocking=MagicMock(return_value=[]),
        plugins=MagicMock(return_value=kwargs.pop("plugins", PLUGINS_READY)),
        exit_dns=MagicMock(return_value=DNS_REACHABLE),
    )
    with (
        patch.object(health, "topology_from_local_cluster", return_value=SimpleNamespace(nodes=[fleet.node])),
        patch.object(health, "make_panel", return_value=fleet.panel),
        patch.object(health, "connection_for_entry", return_value=fleet.conn),
        patch.object(health, "check_container_running", mocks.containers),
        patch.object(health, "check_port_listening", mocks.ports),
        patch.object(health, "check_tls_certificate", mocks.tls),
        patch.object(health, "check_disk_space", return_value=PASSED),
        patch.object(health, "check_node_plugin_host", mocks.plugins),
        patch.object(health, "check_exit_dns", mocks.exit_dns),
        patch.object(health, "deployment_verification_context", return_value=fleet.deployment),
        patch.object(health, "blocking_jobs", mocks.blocking),
        patch("meridian.commands.relay_check.collect_relay_check", return_value=SimpleNamespace(checks=[])) as relay,
    ):
        mocks.relay_check = relay
        health.run(*args, **kwargs)
    return mocks


def test_control_and_exit_server_checks_node_panel_and_subscription_page_containers(fleet: SimpleNamespace) -> None:
    containers = _run(fleet, "exit-a").containers

    assert containers.call_args_list == [
        call(fleet.conn, name, legacy)
        for name, legacy in (
            ("netagent", "remnawave-node"),
            ("ctlsvc-api", "remnawave"),
            ("ctlsvc-db", "remnawave-db"),
            ("ctlsvc-cache", "remnawave-redis"),
            ("ctlsvc-web", "remnawave-subscription-page"),
        )
    ]
    fleet.panel.get_node.assert_called_once_with("node-uuid")


def test_node_hosts_report_plugin_readiness(fleet: SimpleNamespace) -> None:
    plugins = _run(fleet, "exit-a").plugins

    plugins.assert_called_once_with(fleet.conn)


def test_node_hosts_report_encrypted_dns_reachability(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet, "exit-a")

    # Exits resolve only over DoH; a host that reaches no resolver must show up in `check`.
    mocks.exit_dns.assert_called_once_with(fleet.conn)


def test_unready_plugin_host_is_reported_with_its_fix(
    fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    unready = build_verification_check(
        "node_plugins",
        "Node plugin readiness",
        [finding("NODE_PLUGIN_KERNEL", "warning", "Kernel 5.4.0 is older than 5.7", "move to a 5.7+ kernel")],
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a", plugins=unready)

    # A warning is a finding like any other (non-LTS OS, disabled node): exit 4, never 3.
    assert exc_info.value.exit_code == 4
    assert "Fix: move to a 5.7+ kernel" in capsys.readouterr().err


def test_disconnected_node_is_a_finding(fleet: SimpleNamespace) -> None:
    fleet.panel.get_node.return_value = SimpleNamespace(is_disabled=False, is_connected=False)

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a")

    assert exc_info.value.exit_code == 4


def test_unreachable_panel_is_inconclusive_and_server_checks_still_run(fleet: SimpleNamespace) -> None:
    fleet.panel.get_node.side_effect = RemnawaveError("Connection refused")

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a")

    assert exc_info.value.exit_code == 3
    fleet.conn.check_ssh.assert_called_once()


def test_no_target_checks_every_fleet_server_including_relay_hops(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet)

    assert mocks.containers.call_count == 5
    mocks.relay_check.assert_called_once_with("198.51.100.20", "")


def test_a_server_outside_the_fleet_gets_preflight(fleet: SimpleNamespace) -> None:
    report = health.health_report("198.51.100.99", "preflight", [])
    with patch.object(health, "_preflight_report", return_value=report) as preflight, pytest.raises(typer.Exit):
        _run(fleet, "198.51.100.99")

    assert preflight.call_args.args[1] == "198.51.100.99"


def test_json_output_is_one_check_envelope_for_the_whole_fleet(
    fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    from meridian.console import set_json_mode

    set_json_mode(True)
    try:
        _run(fleet)
    except typer.Exit:
        pass
    finally:
        set_json_mode(False)

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "check"
    assert [report["scope"] for report in envelope["data"]["reports"]] == ["server", "relay"]


def test_listener_checks_follow_compiled_public_and_internal_ports(fleet: SimpleNamespace) -> None:
    fleet.deployment = SimpleNamespace(
        public_tcp_ports=(7443,),
        public_udp_ports=(9443,),
        internal_ports={"exit-a/reality": 39001},
        public_tls_ports=(7443,),
        https_routes=(SimpleNamespace(port=7443, tls_sni="edge.example.com", host_header=""),),
    )
    mocks = _run(fleet, "exit-a")

    assert mocks.ports.call_args_list == [
        call(fleet.conn, 7443, transport="tcp"),
        call(fleet.conn, 9443, transport="udp"),
        call(fleet.conn, 39001, transport="any"),
    ]
    mocks.tls.assert_called_once_with(fleet.conn, "edge.example.com", port=7443)


def test_remote_check_measures_data_flow_from_the_deployer_without_the_differential(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet, "exit-a")

    mocks.blocking.assert_called_once_with("198.51.100.10", fleet.deployment, differential=False)


def test_on_server_check_skips_deployer_vantage_data_flow(fleet: SimpleNamespace) -> None:
    fleet.conn.local_mode = True
    mocks = _run(fleet, "exit-a")

    mocks.blocking.assert_not_called()


def test_findings_print_their_fix(capsys: pytest.CaptureFixture[str]) -> None:
    from meridian.commands.check_render import render_verification
    from meridian.diagnostics.evidence import check_from_result

    failed = CheckResult(name="c", status="failed", detail="node is stopped", remediation="docker start remnawave-node")
    check = check_from_result("container:remnawave-node", "Container remnawave-node", failed)

    render_verification("Server health", "exit-a", [check], "findings", "1 problem")

    err = capsys.readouterr().err
    assert "node is stopped" in err
    assert "Fix: docker start remnawave-node" in err
