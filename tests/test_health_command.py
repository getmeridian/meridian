"""`meridian check` dispatches by fleet role and reports on the verification contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from meridian import __version__
from meridian.cluster import ClusterConfig, SubscriptionPageConfig
from meridian.commands import health
from meridian.control.client import ControlServiceError
from meridian.control.models import NodeRuntime, NodeStatus, ServiceHealth
from meridian.control.service import utc_now
from meridian.core.agent_types import NodeStateRef
from meridian.core.fleet import TopologyNode
from meridian.core.verification import build_verification_check
from meridian.diagnostics.checks import CheckResult
from meridian.diagnostics.evidence import finding
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import add_relay_to_intent
from meridian.setup.single_server import single_server_intent
from meridian.verification import VerificationHttpsRoute
from tests.support.node_runtime import node_report

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
OTHER_FLEET_ID = "1f1e2d3c-4b5a-4968-8776-655443322110"
PASSED = CheckResult(name="check", status="passed", detail="healthy")
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
    cluster = ClusterConfig(
        topology_intent=intent, fleet_id=FLEET_ID, subscription_page=SubscriptionPageConfig(deployed=True)
    )
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
    client = MagicMock()
    client.__enter__.return_value = client
    client.health.return_value = ServiceHealth(version=__version__, fleet_id=FLEET_ID, fleet_epoch=1)
    client.node.return_value = NodeStatus(
        node_id="exit-a",
        fleet_epoch=1,
        revision=2,
        sha256="a" * 64,
        spec_sha256="b" * 64,
        applied=NodeStateRef(fleet_epoch=1, revision=2, sha256="a" * 64),
        last_seen_at=utc_now(),
        agent_version="0.1.0",
        runtime=NodeRuntime.model_validate(
            {key: node_report(utc_now())[key] for key in ("health", "versions", "observed_at")}
        ),
    )
    conn = MagicMock(local_mode=False, user="root")
    deployment = SimpleNamespace(
        public_tcp_ports=(443,),
        public_udp_ports=(),
        internal_ports={},
        public_tls_ports=(443,),
        https_routes=(VerificationHttpsRoute(kind="camouflage", port=443, tls_sni="www.example.com"),),
    )
    return SimpleNamespace(cluster=cluster, node=node, client=client, conn=conn, deployment=deployment)


def _run(fleet: SimpleNamespace, *args: str, **kwargs: object) -> SimpleNamespace:
    mocks = SimpleNamespace(
        units=MagicMock(return_value=PASSED),
        ports=MagicMock(return_value=PASSED),
        tls=MagicMock(return_value=PASSED),
        blocking=MagicMock(return_value=[]),
        exit_dns=MagicMock(return_value=DNS_REACHABLE),
    )
    with (
        patch.object(health, "topology_from_local_cluster", return_value=SimpleNamespace(nodes=[fleet.node])),
        patch.object(health, "ControlClient", return_value=fleet.client),
        patch.object(health, "expected_agent_version", return_value=kwargs.pop("agent", "0.1.0")),
        patch.object(health, "connection_for_entry", return_value=fleet.conn),
        patch.object(health, "check_unit_active", mocks.units),
        patch.object(health, "check_port_listening", mocks.ports),
        patch.object(health, "check_tls_certificate", mocks.tls),
        patch.object(health, "check_disk_space", return_value=PASSED),
        patch.object(health, "check_exit_dns", mocks.exit_dns),
        patch.object(health, "deployment_verification_context", return_value=fleet.deployment),
        patch.object(health, "blocking_jobs", mocks.blocking),
        patch("meridian.commands.relay_check.collect_relay_check", return_value=SimpleNamespace(checks=[])) as relay,
    ):
        mocks.relay_check = relay
        health.run(*args, **kwargs)
    return mocks


def test_node_hosts_report_encrypted_dns_reachability(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet, "exit-a")

    # Exits resolve only over DoH; a host that reaches no resolver must show up in `check`.
    mocks.exit_dns.assert_called_once_with(fleet.conn)


def test_no_target_checks_every_fleet_server_including_relay_hops(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet)

    assert mocks.units.call_count == 3
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
        https_routes=(VerificationHttpsRoute(kind="managed", port=7443, tls_sni="edge.example.com"),),
    )
    mocks = _run(fleet, "exit-a")

    assert mocks.ports.call_args_list == [
        call(fleet.conn, 7443, transport="tcp"),
        call(fleet.conn, 9443, transport="udp"),
        call(fleet.conn, 39001, transport="any"),
    ]
    mocks.tls.assert_called_once_with(fleet.conn, "edge.example.com", port=7443)


def test_tls_check_reads_meridians_certificate_before_the_camouflage_sites(fleet: SimpleNamespace) -> None:
    fleet.deployment.https_routes = (
        VerificationHttpsRoute(kind="camouflage", port=443, tls_sni="www.example.com"),
        VerificationHttpsRoute(kind="managed", port=443, tls_sni="198.51.100.10", host_header="198.51.100.10"),
    )
    with patch.object(health, "health_report", wraps=health.health_report) as report:
        mocks = _run(fleet, "exit-a")

    mocks.tls.assert_called_once_with(fleet.conn, "198.51.100.10", port=443)
    title, _scope, checks = report.call_args.args
    assert not title.startswith("TLS")  # the server's heading keeps its name
    assert "TLS certificate on 443" in [check.name for check in checks]


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

    failed = CheckResult(name="c", status="failed", detail="node is stopped", remediation="systemctl start netagent")
    check = check_from_result("unit:netagent.service", "Unit netagent.service", failed)

    render_verification("Server health", "exit-a", [check], "findings", "1 problem")

    err = capsys.readouterr().err
    assert "node is stopped" in err
    assert "Fix: systemctl start netagent" in err


@pytest.mark.parametrize(
    ("service", "agent", "message"),
    [("9.0.0", "0.1.0", "newer than this Meridian"), (__version__, "0.2.0", "this Meridian installs 0.2.0")],
    ids=["newer-service", "older-agent"],
)
def test_version_skew_is_a_check_finding(
    fleet: SimpleNamespace, capsys: pytest.CaptureFixture[str], service: str, agent: str, message: str
) -> None:
    fleet.client.health.return_value = ServiceHealth(version=service, fleet_id=FLEET_ID, fleet_epoch=1)

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a", agent=agent)

    assert exc_info.value.exit_code == 4
    assert message in capsys.readouterr().err


def test_control_and_exit_server_checks_units_service_and_agent(fleet: SimpleNamespace) -> None:
    mocks = _run(fleet, "exit-a")

    assert [item.args[1] for item in mocks.units.call_args_list] == [
        "ctld.service",
        "netagent.service",
        "netcore.service",
    ]
    fleet.client.health.assert_called_once_with()
    fleet.client.node.assert_called_once_with("exit-a")


def test_an_unreachable_control_service_is_inconclusive_and_server_checks_still_run(
    fleet: SimpleNamespace,
) -> None:
    fleet.client.health.side_effect = ControlServiceError("no answer", retryable=True)

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a")

    assert exc_info.value.exit_code == 3
    fleet.client.node.assert_not_called()
    fleet.conn.check_ssh.assert_called_once()


def test_a_service_answering_for_another_fleet_is_a_finding(fleet: SimpleNamespace) -> None:
    fleet.client.health.return_value = ServiceHealth(version="4.0.0", fleet_id=OTHER_FLEET_ID, fleet_epoch=1)

    with pytest.raises(typer.Exit) as exc_info:
        _run(fleet, "exit-a")

    assert exc_info.value.exit_code == 4


@pytest.mark.parametrize(
    ("health_status", "check", "stamp", "exit_code"),
    [
        ("unhealthy", "pass", None, 4),
        ("healthy", "fail", None, 4),
        ("unhealthy", "fail", "2026-01-01T00:00:00Z", 3),
    ],
)
def test_check_uses_fresh_runtime_health(
    fleet: SimpleNamespace,
    health_status: str,
    check: str,
    stamp: str | None,
    exit_code: int,
) -> None:
    report = node_report(stamp or utc_now(), health=health_status, check=check)
    fleet.client.node.return_value = NodeStatus.model_validate(
        {
            **fleet.client.node.return_value.model_dump(),
            "runtime": {key: report[key] for key in ("health", "versions", "observed_at")},
        }
    )
    with pytest.raises(typer.Exit) as raised:
        _run(fleet, "exit-a")
    assert raised.value.exit_code == exit_code
