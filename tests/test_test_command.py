"""Connectivity-test command contracts, exits, and CLI wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.commands.test import collect_test_result, fleet_test_targets, recovery_hint
from meridian.connectivity import ConnectivityChecks
from meridian.core.topology import RoutingGatewayIntent
from meridian.core.verification import (
    RecoveryHint,
    TransportResult,
    VerificationCheck,
    VerificationContext,
    VerificationFinding,
    VerificationStatus,
    VerificationTarget,
    aggregate_verification,
    build_verification_check,
)
from meridian.core.verification import (
    TestResult as CoreTestResult,
)
from meridian.servers import ServerEntry, ServerRegistry
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent

_IP = "198.51.100.20"
runner = CliRunner()


def _check(status: VerificationStatus, *, message: str = "") -> VerificationCheck:
    return VerificationCheck(
        id="sample",
        name="Sample",
        status=status,
        findings=[VerificationFinding(code="SAMPLE", status=status, message=message or f"Sample {status}")],
    )


def _result(
    status: VerificationStatus,
    *,
    scope: Literal["basic", "full"] = "full",
    message: str = "",
) -> CoreTestResult:
    checks = [_check(status, message=message)]
    aggregate = aggregate_verification(checks)
    return CoreTestResult(
        target=VerificationTarget(requested=_IP, resolved_ip=_IP),
        context=VerificationContext(mode="generic"),
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
        checks=checks,
        scope=scope,
        client="alice" if scope == "full" else "",
    )


def test_collect_test_result_preserves_scope_client_and_exit(tmp_path: Path) -> None:
    from meridian.cluster import ClusterConfig

    result = collect_test_result(
        _IP,
        "",
        "",
        "",
        client="alice",
        registry=ServerRegistry(tmp_path / "servers.json"),
        cluster=ClusterConfig(),
        connectivity_collector=lambda *_args, **_kwargs: ConnectivityChecks(
            checks=[_check("failed")],
            client="alice",
        ),
    )

    assert result.scope == "full"
    assert result.client == "alice"
    assert result.verdict == "findings"
    assert result.exit_code == 4


def test_basic_scope_can_pass_without_a_client(tmp_path: Path) -> None:
    from meridian.cluster import ClusterConfig

    result = collect_test_result(
        _IP,
        "",
        "",
        "",
        basic=True,
        registry=ServerRegistry(tmp_path / "servers.json"),
        cluster=ClusterConfig(),
        connectivity_collector=lambda *_args, **_kwargs: ConnectivityChecks(checks=[_check("passed")]),
    )

    assert result.scope == "basic"
    assert result.client == ""
    assert result.exit_code == 0


def test_basic_scope_rejects_explicit_client_as_json_user_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    result = runner.invoke(app, ["test", _IP, "--basic", "--client", "alice", "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["command"] == "test"
    assert payload["status"] == "failed"
    assert payload["errors"][0]["category"] == "user"
    assert payload["summary"]["text"] == "--client cannot be used with --basic"


def test_known_host_domain_reuses_its_configured_tls_sni(tmp_path: Path) -> None:
    from meridian.cluster import ClusterConfig
    from meridian.core.topology import ProtocolPathIntent
    from meridian.servers import ServerEntry

    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10", name="control"))
    registry.add(ServerEntry(id="srv-exit", host=_IP, name="exit"))
    cluster = ClusterConfig(
        topology_intent=single_exit_intent(
            paths=[
                ProtocolPathIntent(
                    id="reality-a",
                    protocol="reality",
                    reality_sni="www.example.com",
                ),
                ProtocolPathIntent(
                    id="xhttp-a",
                    protocol="xhttp",
                    tls_sni="tls.example.com",
                    host="edge.example.com",
                    path="transport-a",
                ),
            ]
        )
    )

    def collect(_cluster: ClusterConfig, deployment, *_args, **_kwargs) -> ConnectivityChecks:
        routes = [route for route in deployment.https_routes if route.host_header == "edge.example.com"]
        assert [(route.tls_sni, route.port) for route in routes] == [("tls.example.com", 443)]
        return ConnectivityChecks(checks=[_check("passed")])

    result = collect_test_result(
        _IP,
        "edge.example.com",
        "",
        "",
        basic=True,
        registry=registry,
        cluster=cluster,
        connectivity_collector=collect,
    )

    assert result.exit_code == 0


def test_test_json_findings_emit_one_envelope_and_exit_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.test.collect_test_result", lambda *_args, **_kwargs: _result("failed"))

    result = runner.invoke(app, ["test", _IP, "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["command"] == "test"
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 4
    assert payload["data"]["scope"] == "full"
    assert payload["data"]["client"] == "alice"


def test_test_outputs_redact_credential_shaped_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    secret = "SUPERSECRET-PRIVATE-CREDENTIAL"
    result_with_secret = _result("failed", message=f'invalid "password": {secret}')
    monkeypatch.setattr("meridian.commands.test.collect_test_result", lambda *_args, **_kwargs: result_with_secret)

    human = runner.invoke(app, ["test", _IP])
    machine = runner.invoke(app, ["test", _IP, "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert human.exit_code == 4
    assert secret not in human.output
    assert "redacted" in human.output
    assert machine.exit_code == 4
    assert secret not in machine.stdout
    assert json.loads(machine.stdout)["data"]["checks"][0]["findings"][0]["message"].endswith("[redacted]")


def test_test_json_inconclusive_is_typed_system_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.test.collect_test_result", lambda *_args, **_kwargs: _result("skipped"))

    result = runner.invoke(app, ["test", _IP, "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["errors"][0]["code"] == "MERIDIAN_TEST_INCONCLUSIVE"
    assert payload["data"]["verdict"] == "inconclusive"


def test_global_json_is_supported_for_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.test.collect_test_result", lambda *_args, **_kwargs: _result("passed"))

    result = runner.invoke(app, ["--json", "test", _IP])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 0
    assert json.loads(result.stdout)["command"] == "test"


def test_test_help_documents_full_and_basic_controls() -> None:
    result = runner.invoke(app, ["test", "--help"])
    output = unstyle(result.output)

    assert result.exit_code == 0
    for option in ("--json", "--timeout", "--client", "--basic", "--domain", "--sni"):
        assert option in output


def test_invalid_timeout_preserves_json_error_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    result = runner.invoke(app, ["test", _IP, "--timeout", "not-a-number", "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["command"] == "test"
    assert payload["status"] == "failed"
    assert payload["errors"][0]["category"] == "user"


def _fleet(servers_file: Path) -> tuple[ServerRegistry, ClusterConfig]:
    """A control+exit server, a second exit, and a relay into the first exit."""
    from meridian.setup.editor import add_exit_to_intent, add_relay_to_intent
    from meridian.setup.single_server import single_server_intent

    registry = ServerRegistry(servers_file)
    for host, name in (("198.51.100.10", "exit-a"), ("198.51.100.11", "exit-b"), ("198.51.100.20", "relay-a")):
        registry.add(ServerEntry(host=host, name=name))
    intent = single_server_intent(None, server_ref=registry.find("exit-a").id, sni="www.example.com")
    intent = add_exit_to_intent(
        intent, server_ref=registry.find("exit-b").id, title="exit-b", reality_sni="www.example.com"
    )
    intent = add_relay_to_intent(
        intent, server_ref=registry.find("relay-a").id, title="relay-a", exit_ref="main", listen_port=8443
    )
    return registry, ClusterConfig(topology_intent=intent)


def test_bare_test_covers_every_exit_then_each_relay_entry_then_each_gateway(servers_file: Path) -> None:
    registry, cluster = _fleet(servers_file)
    registry.add(ServerEntry(host="198.51.100.40", name="gw-a"))
    assert cluster.topology_intent is not None
    gateway = RoutingGatewayIntent(id="gw-a", server_ref=registry.find("gw-a").id, bridge_path_ref="main-reality")
    cluster.topology_intent = cluster.topology_intent.model_copy(update={"routing_gateways": [gateway]})

    targets = fleet_test_targets(registry, cluster)

    assert [entry.name for entry in targets] == ["exit-a", "exit-b", "relay-a", "gw-a"]


def test_bare_test_keeps_single_target_resolution_for_one_server(servers_file: Path) -> None:
    from meridian.setup.single_server import single_server_intent

    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="exit-a"))
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref=registry.find("exit-a").id))

    assert fleet_test_targets(registry, cluster) == []


def test_bare_fleet_test_emits_one_envelope_with_every_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    entries = [ServerEntry(host="198.51.100.10", name="exit-a"), ServerEntry(host="198.51.100.20", name="relay-a")]
    monkeypatch.setattr("meridian.commands.test.fleet_test_targets", lambda: entries)
    seen: list[str] = []

    def collect(_target: str, _domain: str, _sni: str, server: str, **_kwargs: object) -> CoreTestResult:
        seen.append(server)
        return _result("failed" if server == "relay-a" else "passed")

    monkeypatch.setattr("meridian.commands.test.collect_test_result", collect)

    result = runner.invoke(app, ["test", "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert seen == ["exit-a", "relay-a"]
    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert [one["verdict"] for one in payload["data"]["results"]] == ["passed", "findings"]
    assert payload["data"]["verdict"] == "findings"
    assert payload["summary"]["text"] == "1 of 2 servers had failed checks."


def test_fleet_shrunk_to_one_entry_tests_it_while_others_stay_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    """After `node remove`, the old servers stay saved; bare `test` must not ask which one."""
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr(
        "meridian.commands.test.fleet_test_targets", lambda: [ServerEntry(host="198.51.100.10", name="exit-a")]
    )
    seen: list[str] = []

    def collect(_target: str, _domain: str, _sni: str, server: str, **_kwargs: object) -> CoreTestResult:
        seen.append(server)
        return _result("passed")

    monkeypatch.setattr("meridian.commands.test.collect_test_result", collect)

    result = runner.invoke(app, ["test", "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 0
    assert seen == ["exit-a"]


def _tcp(status: VerificationStatus) -> VerificationCheck:
    finding = VerificationFinding(code="TCP_443", status=status, message="TCP/443")
    return build_verification_check("tcp_reachability", "TCP/443", [finding])


def _transport(outcome: str) -> TransportResult:
    return TransportResult(check_id="proxy:reality", label="Reality", outcome=outcome)


_REPLACE = RecoveryHint(code="replace_exit", exit="exit-a", command="meridian node replace exit-a NEW_SERVER")


@pytest.mark.parametrize(
    ("exit_ref", "tested", "tcp", "transports", "expected"),
    [
        ("srv-exit", "srv-exit", "failed", [_transport("unreachable"), _transport("failed")], _REPLACE),
        ("srv-control", "srv-control", "failed", [], RecoveryHint(code="control_unreachable", exit="exit-a")),
        ("srv-exit", "srv-exit", "failed", [_transport("unreachable"), _transport("ok")], None),
        ("srv-exit", "srv-exit", "passed", [_transport("no_traffic")], None),
        ("srv-exit", "srv-relay", "failed", [_transport("unreachable")], None),
    ],
    ids=["exit-answers-nothing", "control-answers-nothing", "one-path-works", "tcp-answers", "not-an-exit"],
)
def test_recovery_hint_only_when_nothing_on_an_exit_answers(
    exit_ref: str,
    tested: str,
    tcp: VerificationStatus,
    transports: list[TransportResult],
    expected: RecoveryHint | None,
) -> None:
    intent = single_exit_intent(control="srv-control", exit_ref=exit_ref)

    assert recovery_hint([_tcp(tcp)], transports, intent, tested) == expected


def test_recovery_hint_stays_out_of_json_when_absent() -> None:
    assert "recovery" not in _result("passed").to_data()
