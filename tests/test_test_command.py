"""Connectivity-test command contracts, exits, and CLI wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.commands.test import collect_test_result
from meridian.connectivity import ConnectivityChecks
from meridian.core.verification import (
    TestResult as CoreTestResult,
)
from meridian.core.verification import (
    VerificationCheck,
    VerificationContext,
    VerificationFinding,
    VerificationStatus,
    VerificationTarget,
    aggregate_verification,
)
from meridian.servers import ServerRegistry

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
    from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
    from meridian.servers import ServerEntry

    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10", name="control"))
    registry.add(ServerEntry(id="srv-exit", host=_IP, name="exit"))
    cluster = ClusterConfig(
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
                        ),
                        ProtocolPathIntent(
                            id="xhttp-a",
                            protocol="xhttp",
                            tls_sni="tls.example.com",
                            host="edge.example.com",
                            path="transport-a",
                        ),
                    ],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
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

    assert result.exit_code == 0
    for option in ("--json", "--timeout", "--client", "--basic", "--domain", "--sni"):
        assert option in result.output


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
