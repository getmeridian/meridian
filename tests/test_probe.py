"""Typed external probe checks and command orchestration."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.commands.probe import collect_probe_result
from meridian.core.verification import (
    ProbeResult,
    VerificationCheck,
    VerificationContext,
    VerificationFinding,
    VerificationStatus,
    VerificationTarget,
)
from meridian.diagnostics.external import (
    check_domain_root,
    check_http_response,
    check_internal_ports,
    check_legacy_tls,
    check_port_surface,
    check_proxy_paths,
    check_secret_paths,
    check_sni_camouflage,
    check_sni_consistency,
    check_tls_certificate,
)
from meridian.diagnostics.network import https_get
from meridian.diagnostics.probe import run_probe_checks
from meridian.verification import DeploymentVerificationContext, VerificationHttpsRoute

_IP = "198.51.100.20"
runner = CliRunner()


def test_probe_cancellation_does_not_wait_for_worker_timeouts() -> None:
    script = textwrap.dedent(
        """
        import os
        import signal
        import threading

        from meridian.diagnostics.probe import _run_interruptible_jobs

        release = threading.Event()

        def blocked_check():
            release.wait(10)

        timer = threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            _run_interruptible_jobs([blocked_check, blocked_check])
        except KeyboardInterrupt:
            print("interrupted")
        else:
            raise SystemExit("SIGINT did not interrupt probe workers")
        finally:
            release.set()
            timer.cancel()
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "interrupted"


def test_https_get_keeps_request_details_off_argv_and_bounds_wall_clock() -> None:
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"status": 200, "headers": {"server": "nginx"}, "body": "b2s="}),
    )
    with patch("meridian.diagnostics.network.subprocess.run", return_value=completed) as run:
        status, headers, body = https_get(
            _IP,
            "/secret-path",
            timeout=1.25,
            server_name="example.test",
            extra_headers={"Authorization": "Bearer secret"},
        )

    assert (status, headers, body) == (200, {"server": "nginx"}, b"ok")
    argv = run.call_args.args[0]
    assert "/secret-path" not in argv
    assert "Bearer secret" not in argv
    request = json.loads(run.call_args.kwargs["input"])
    assert request["path"] == "/secret-path"
    assert request["extra_headers"] == {"Authorization": "Bearer secret"}
    assert run.call_args.kwargs["timeout"] == 1.25


def test_https_get_timeout_is_unavailable_evidence() -> None:
    with patch(
        "meridian.diagnostics.network.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="python", timeout=0.1),
    ):
        assert https_get(_IP, "/", timeout=0.1) == (0, {}, b"")


def test_tls_handshake_timeout_is_killable() -> None:
    from meridian.diagnostics.network import get_certificate_der, negotiate_alpn, probe_tls_version

    timeout = subprocess.TimeoutExpired(cmd="python", timeout=0.1)
    with patch("meridian.diagnostics.network.subprocess.run", side_effect=timeout):
        assert get_certificate_der(_IP, "www.example.com", timeout=0.1) == b""
        assert negotiate_alpn(_IP, "www.example.com", ["h2"], timeout=0.1) is None
        assert probe_tls_version(_IP, "www.example.com", "TLSv1", timeout=0.1) == "unavailable"


def _check(status: VerificationStatus) -> VerificationCheck:
    return VerificationCheck(
        id="sample",
        name="Sample",
        status=status,
        findings=[
            VerificationFinding(
                code="SAMPLE",
                status=status,
                message=f"Sample {status}",
            )
        ],
    )


def _result(status: VerificationStatus) -> ProbeResult:
    return ProbeResult.from_checks(
        target=VerificationTarget(requested=_IP, resolved_ip=_IP),
        context=VerificationContext(mode="generic"),
        checks=[_check(status)],
    )


def test_open_plaintext_management_port_is_a_failure() -> None:
    def connect(_ip: str, port: int, timeout: float = 3) -> bool:
        return port in {443, 3000}

    with patch("meridian.diagnostics.external.tcp_connect", side_effect=connect):
        result = check_port_surface(_IP)

    assert result.status == "failed"
    assert any(finding.code == "UNEXPECTED_PORT_3000" for finding in result.findings)
    assert not any(finding.code == "NO_UNEXPECTED_PORTS" for finding in result.findings)


def test_closed_required_port_is_a_failure() -> None:
    with patch("meridian.diagnostics.external.tcp_connect", return_value=False):
        result = check_port_surface(_IP, expected_tcp_ports=(443, 8443))

    assert result.status == "failed"
    assert {finding.code for finding in result.findings} >= {"PORT_443_CLOSED", "PORT_8443_CLOSED"}


def test_unavailable_https_is_skipped_not_passed() -> None:
    with patch("meridian.diagnostics.external.https_get", return_value=(0, {}, b"")):
        result = check_http_response(_IP, meridian_mode=False)

    assert result.status == "skipped"
    assert result.findings[0].code == "HTTPS_UNAVAILABLE"


def test_generic_site_content_is_observed_without_meridian_policy_failure() -> None:
    with patch(
        "meridian.diagnostics.external.https_get",
        side_effect=[(200, {"server": "example"}, b"website"), (404, {}, b"")],
    ):
        result = check_http_response(_IP, meridian_mode=False)

    assert result.status == "passed"


def test_meridian_root_content_is_a_failure() -> None:
    with patch(
        "meridian.diagnostics.external.https_get",
        side_effect=[(200, {"server": "nginx"}, b"Meridian"), (404, {}, b"")],
    ):
        result = check_http_response(_IP, meridian_mode=True)

    assert result.status == "failed"
    assert {finding.code for finding in result.findings} >= {"UNEXPECTED_ROOT_STATUS", "ROOT_PRODUCT_LEAK"}


def test_tls_requires_a_peer_certificate() -> None:
    with patch("meridian.diagnostics.external.get_certificate_der", return_value=b""):
        result = check_tls_certificate(_IP)

    assert result.status == "failed"
    assert result.findings[0].code == "TLS_HANDSHAKE_FAILED"


def test_tls_rejects_untrusted_or_wrong_identity_certificate() -> None:
    with (
        patch("meridian.diagnostics.external.get_certificate_der", return_value=b"certificate"),
        patch(
            "meridian.diagnostics.external.certificate_validation_error",
            return_value="hostname mismatch",
        ),
        patch("meridian.diagnostics.external.certificate_text", return_value=""),
    ):
        result = check_tls_certificate(_IP, server_name="edge.example.com")

    assert result.status == "failed"
    assert any(finding.code == "TLS_CERTIFICATE_INVALID" for finding in result.findings)


def test_tls_network_validation_error_is_inconclusive() -> None:
    with (
        patch("meridian.diagnostics.external.get_certificate_der", return_value=b"certificate"),
        patch("meridian.diagnostics.external.certificate_validation_error", return_value=None),
        patch("meridian.diagnostics.external.certificate_text", return_value=""),
    ):
        result = check_tls_certificate(_IP, server_name="edge.example.com")

    assert result.status == "skipped"
    assert any(finding.code == "TLS_VALIDATION_UNAVAILABLE" for finding in result.findings)


def test_tls_does_not_require_openssl_details_for_valid_certificate() -> None:
    with (
        patch("meridian.diagnostics.external.get_certificate_der", return_value=b"certificate"),
        patch("meridian.diagnostics.external.certificate_validation_error", return_value=""),
        patch("meridian.diagnostics.external.certificate_text", return_value=""),
    ):
        result = check_tls_certificate(_IP, server_name="edge.example.com")

    assert result.status == "passed"


def test_incomplete_sni_evidence_is_skipped() -> None:
    with patch("meridian.diagnostics.external.get_certificate_der", side_effect=[b"cert", b"", b""]):
        result = check_sni_consistency(_IP, "www.example.com", meridian_mode=True)

    assert result.status == "skipped"


def test_sni_edge_variation_is_inconclusive_for_unmanaged_origin() -> None:
    with (
        patch("meridian.diagnostics.external.get_certificate_der", side_effect=[b"one", b"two", b"three"]),
        patch("meridian.diagnostics.external.certificate_identity", side_effect=["one", "two", "three"]),
    ):
        result = check_sni_consistency(_IP, "www.example.com", meridian_mode=False)

    assert result.status == "skipped"
    assert result.findings[0].code == "SNI_EDGE_VARIATION"


def test_sni_variation_fails_for_meridian_managed_certificate_route() -> None:
    with (
        patch("meridian.diagnostics.external.get_certificate_der", side_effect=[b"one", b"two", b"three"]),
        patch("meridian.diagnostics.external.certificate_identity", side_effect=["one", "two", "three"]),
    ):
        result = check_sni_consistency(_IP, "edge.example.com", meridian_mode=True)

    assert result.status == "failed"
    assert result.findings[0].code == "SNI_INCONSISTENT"


def test_camouflage_origin_uses_bounded_resolution_before_tls() -> None:
    with (
        patch("meridian.diagnostics.external.resolve_hostname", return_value="203.0.113.20") as resolve,
        patch("meridian.diagnostics.external.get_certificate_der", return_value=b"certificate") as certificate,
        patch("meridian.diagnostics.external.certificate_validation_error", return_value=""),
        patch("meridian.diagnostics.external.certificate_identity", return_value="identity"),
    ):
        result = check_sni_camouflage(_IP, "www.example.com", timeout=2)

    assert result.status == "passed"
    resolve.assert_called_once_with("www.example.com", timeout=2)
    assert certificate.call_args_list[1].args == ("203.0.113.20", "www.example.com")


def test_camouflage_certificate_variation_is_inconclusive_across_valid_edges() -> None:
    with (
        patch("meridian.diagnostics.external.resolve_hostname", return_value="203.0.113.20"),
        patch("meridian.diagnostics.external.get_certificate_der", side_effect=[b"server", b"origin"]),
        patch("meridian.diagnostics.external.certificate_validation_error", return_value=""),
        patch("meridian.diagnostics.external.certificate_identity", side_effect=["server", "origin"]),
    ):
        result = check_sni_camouflage(_IP, "www.example.com", timeout=2)

    assert result.status == "skipped"
    assert result.findings[0].code == "CAMOUFLAGE_EDGE_VARIATION"


def test_camouflage_rejects_untrusted_or_wrong_hostname_certificate() -> None:
    with (
        patch("meridian.diagnostics.external.resolve_hostname", return_value="203.0.113.20"),
        patch("meridian.diagnostics.external.get_certificate_der", side_effect=[b"server", b"origin"]),
        patch(
            "meridian.diagnostics.external.certificate_validation_error",
            side_effect=["hostname mismatch", ""],
        ),
    ):
        result = check_sni_camouflage(_IP, "www.example.com", timeout=2)

    assert result.status == "failed"
    assert result.findings[0].code == "CAMOUFLAGE_CERTIFICATE_INVALID"


def test_legacy_tls_is_inconclusive_when_local_stack_cannot_probe() -> None:
    with patch(
        "meridian.diagnostics.external._tls_version_result",
        side_effect=["rejected", "unavailable"],
    ):
        result = check_legacy_tls(_IP, "www.example.com")

    assert result.status == "skipped"
    assert result.findings[0].code == "LEGACY_TLS_INCONCLUSIVE"


@pytest.mark.parametrize(
    ("route_kind", "expected"),
    [("managed", "failed"), ("camouflage", "skipped"), ("generic", "warning")],
)
def test_legacy_tls_policy_is_route_aware(route_kind: str, expected: str) -> None:
    with patch("meridian.diagnostics.external._tls_version_result", side_effect=["accepted", "rejected"]):
        result = check_legacy_tls(_IP, "www.example.com", route_kind=route_kind)  # type: ignore[arg-type]

    assert result.status == expected


@pytest.mark.parametrize(("meridian_mode", "expected"), [(False, "warning"), (True, "failed")])
def test_proxy_path_differential_respects_probe_mode(meridian_mode: bool, expected: str) -> None:
    responses: list[tuple[int, dict[str, str], bytes]] = [
        (404, {}, b"base"),
        (200, {}, b"different"),
        *[(404, {}, b"base")] * 6,
    ]
    with patch("meridian.diagnostics.external.https_get", side_effect=responses):
        result = check_proxy_paths(_IP, meridian_mode=meridian_mode)

    assert result.status == expected


def test_proxy_path_detects_equal_length_body_differential() -> None:
    responses: list[tuple[int, dict[str, str], bytes]] = [
        (404, {}, b"base"),
        (404, {}, b"leak"),
        *[(404, {}, b"base")] * 6,
    ]
    with patch("meridian.diagnostics.external.https_get", side_effect=responses):
        result = check_proxy_paths(_IP, meridian_mode=True)

    assert result.status == "failed"
    assert result.findings[0].code == "PROXY_PATH_DIFFERENTIAL"


def test_domain_root_uses_domain_for_sni_and_accepts_hardened_status() -> None:
    calls: list[dict[str, object]] = []

    def request(_ip: str, _path: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        calls.append(kwargs)
        return (403, {}, b"") if len(calls) == 1 else (404, {}, b"")

    with patch("meridian.diagnostics.external.https_get", side_effect=request):
        result = check_domain_root(_IP, "edge.example.com")

    assert result.status == "passed"
    assert all(call["server_name"] == "edge.example.com" for call in calls)
    assert all(call["host_header"] == "edge.example.com" for call in calls)


def test_unavailable_common_panel_paths_make_isolation_inconclusive() -> None:
    with patch(
        "meridian.diagnostics.external.https_get",
        side_effect=[*[(0, {}, b"")] * 5, (200, {}, b"")],
    ):
        result = check_secret_paths(_IP, "secret", server_name="edge.example.com")

    assert result.status == "skipped"
    assert any(finding.code == "COMMON_PANEL_PATH_UNAVAILABLE" for finding in result.findings)


def test_exposed_v4_allocation_fails_internal_port_check() -> None:
    with patch("meridian.diagnostics.external.tcp_connect", side_effect=lambda _ip, port, timeout=3: port == 39001):
        result = check_internal_ports(_IP, {"exit-a/reality": 39001, "exit-a/xhttp": 39002})

    assert result.status == "failed"
    assert "exit-a/reality=39001" in result.findings[0].message


def test_hysteria_only_probe_does_not_assume_tcp_https() -> None:
    context = DeploymentVerificationContext(
        kind="v4",
        roles=("exit:exit-a",),
        protocols=("hysteria2",),
        public_tcp_ports=(),
        public_udp_ports=(443,),
        endpoint_addresses=(_IP,),
    )
    with (
        patch("meridian.diagnostics.external.tcp_connect", return_value=False),
        patch("meridian.diagnostics.external.reverse_hostname", return_value=""),
        patch("meridian.diagnostics.probe.check_tls_certificate") as tls_check,
    ):
        checks = run_probe_checks(_IP, context, timeout=1)

    assert [check.id for check in checks] == ["port_surface", "reverse_dns", "udp_surface"]
    assert checks[-1].status == "skipped"
    tls_check.assert_not_called()


def test_reality_camouflage_uses_custom_port_without_managed_web_policy() -> None:
    context = DeploymentVerificationContext(
        kind="v4",
        roles=("exit:exit-a",),
        protocols=("reality",),
        reality_snis=("www.example.com",),
        public_tcp_ports=(8443,),
        public_tls_ports=(8443,),
        endpoint_addresses=(_IP,),
    )
    passed = _check("passed")
    with (
        patch("meridian.diagnostics.probe.check_port_surface", return_value=passed),
        patch("meridian.diagnostics.probe.check_reverse_dns", return_value=passed),
        patch("meridian.diagnostics.probe.check_http_response", return_value=passed) as http,
        patch("meridian.diagnostics.probe.check_tls_certificate", return_value=passed) as tls,
        patch("meridian.diagnostics.probe.check_sni_consistency", return_value=passed),
        patch("meridian.diagnostics.probe.check_http2_support", return_value=passed),
        patch("meridian.diagnostics.probe.check_legacy_tls", return_value=passed),
        patch("meridian.diagnostics.probe.check_sni_camouflage", return_value=passed),
        patch("meridian.diagnostics.probe.check_proxy_paths") as proxy_paths,
        patch("meridian.diagnostics.probe.check_websocket_upgrade") as websocket,
    ):
        checks = run_probe_checks(_IP, context, timeout=1)

    assert checks
    assert http.call_args.kwargs == {
        "meridian_mode": False,
        "camouflage_origin": True,
        "server_name": "www.example.com",
        "host_header": "",
        "port": 8443,
        "timeout": 1,
    }
    assert tls.call_args.kwargs["port"] == 8443
    proxy_paths.assert_not_called()
    websocket.assert_not_called()


def test_generic_domain_does_not_receive_meridian_web_policy() -> None:
    context = DeploymentVerificationContext(
        domains=("ordinary.example.com",),
        domain_ports={"ordinary.example.com": 443},
    )
    passed = _check("passed")
    with (
        patch("meridian.diagnostics.probe.check_port_surface", return_value=passed),
        patch("meridian.diagnostics.probe.check_reverse_dns", return_value=passed),
        patch("meridian.diagnostics.probe.check_http_response", return_value=passed) as http,
        patch("meridian.diagnostics.probe.check_tls_certificate", return_value=passed),
        patch("meridian.diagnostics.probe.check_sni_consistency", return_value=passed),
        patch("meridian.diagnostics.probe.check_http2_support", return_value=passed),
        patch("meridian.diagnostics.probe.check_legacy_tls", return_value=passed),
        patch("meridian.diagnostics.probe.check_proxy_paths") as proxy_paths,
        patch("meridian.diagnostics.probe.check_domain_root") as domain_root,
    ):
        run_probe_checks(_IP, context, timeout=1)

    assert http.call_args.kwargs["meridian_mode"] is False
    proxy_paths.assert_not_called()
    domain_root.assert_not_called()


def test_probe_preserves_split_sni_and_host_for_every_managed_route() -> None:
    routes = (
        VerificationHttpsRoute(
            kind="managed",
            port=443,
            tls_sni="tls-a.example.com",
            host_header="host-a.example.com",
        ),
        VerificationHttpsRoute(
            kind="managed",
            port=8443,
            tls_sni="tls-b.example.com",
            host_header="host-b.example.com",
        ),
    )
    context = DeploymentVerificationContext(
        kind="v4",
        protocols=("xhttp", "wss"),
        public_tcp_ports=(443, 8443),
        public_tls_ports=(443, 8443),
        https_routes=routes,
    )
    passed = _check("passed")
    with (
        patch("meridian.diagnostics.probe.check_port_surface", return_value=passed),
        patch("meridian.diagnostics.probe.check_reverse_dns", return_value=passed),
        patch("meridian.diagnostics.probe.check_http_response", return_value=passed),
        patch("meridian.diagnostics.probe.check_tls_certificate", return_value=passed) as certificate,
        patch("meridian.diagnostics.probe.check_sni_consistency", return_value=passed),
        patch("meridian.diagnostics.probe.check_http2_support", return_value=passed),
        patch("meridian.diagnostics.probe.check_legacy_tls", return_value=passed),
        patch("meridian.diagnostics.probe.check_proxy_paths", return_value=passed),
        patch("meridian.diagnostics.probe.check_websocket_upgrade", return_value=passed),
        patch("meridian.diagnostics.probe.check_root_indistinguishable", return_value=passed),
        patch("meridian.diagnostics.probe.check_domain_root", return_value=passed) as domain_root,
    ):
        run_probe_checks(_IP, context, timeout=1)

    assert len(certificate.call_args_list) == 2
    assert {(call.kwargs["server_name"], call.kwargs["port"]) for call in certificate.call_args_list} == {
        ("tls-a.example.com", 443),
        ("tls-b.example.com", 8443),
    }
    assert {(call.args[1], call.kwargs["server_name"], call.kwargs["port"]) for call in domain_root.call_args_list} == {
        ("host-a.example.com", "tls-a.example.com", 443),
        ("host-b.example.com", "tls-b.example.com", 8443),
    }


def test_managed_ip_route_runs_root_and_panel_checks() -> None:
    context = DeploymentVerificationContext(
        kind="v4",
        roles=("control",),
        public_tcp_ports=(443,),
        public_tls_ports=(443,),
        https_routes=(VerificationHttpsRoute(kind="managed", port=443, panel=True),),
        panel_secret_path="secret",
    )
    passed = _check("passed")
    with (
        patch("meridian.diagnostics.probe.check_port_surface", return_value=passed),
        patch("meridian.diagnostics.probe.check_reverse_dns", return_value=passed),
        patch("meridian.diagnostics.probe.check_http_response", return_value=passed),
        patch("meridian.diagnostics.probe.check_tls_certificate", return_value=passed),
        patch("meridian.diagnostics.probe.check_sni_consistency", return_value=passed),
        patch("meridian.diagnostics.probe.check_http2_support", return_value=passed),
        patch("meridian.diagnostics.probe.check_legacy_tls", return_value=passed),
        patch("meridian.diagnostics.probe.check_proxy_paths", return_value=passed),
        patch("meridian.diagnostics.probe.check_websocket_upgrade", return_value=passed),
        patch("meridian.diagnostics.probe.check_root_indistinguishable", return_value=passed) as root,
        patch("meridian.diagnostics.probe.check_domain_root", return_value=passed),
        patch("meridian.diagnostics.probe.check_secret_paths", return_value=passed) as secret,
    ):
        run_probe_checks(_IP, context, timeout=1)

    root.assert_called_once()
    secret.assert_called_once()


def test_collect_probe_result_uses_typed_aggregate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from meridian.cluster import ClusterConfig
    from meridian.servers import ServerRegistry

    monkeypatch.setattr("meridian.commands.probe.run_probe_checks", lambda *_args, **_kwargs: [_check("failed")])

    result = collect_probe_result(
        _IP,
        "",
        registry=ServerRegistry(tmp_path / "servers.json"),
        cluster=ClusterConfig(),
    )

    assert result.verdict == "findings"
    assert result.exit_code == 4
    assert result.context.mode == "generic"


def test_explicit_external_probe_reports_corrupt_v4_registry_as_inconclusive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meridian.cluster import ClusterConfig
    from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
    from meridian.servers import ServerRegistry

    registry_path = tmp_path / "servers.json"
    registry_path.write_text("broken", encoding="utf-8")
    cluster = ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="exit",
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
    )
    monkeypatch.setattr("meridian.commands.probe.run_probe_checks", lambda *_args, **_kwargs: [_check("passed")])

    result = collect_probe_result(
        _IP,
        "",
        registry=ServerRegistry(registry_path),
        cluster=cluster,
    )

    assert result.verdict == "inconclusive"
    assert result.exit_code == 3
    assert result.checks[0].findings[0].code == "LOCAL_CONTEXT_UNAVAILABLE"


def test_probe_json_findings_emit_one_envelope_and_exit_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.probe.collect_probe_result", lambda *_args, **_kwargs: _result("failed"))

    result = runner.invoke(app, ["probe", _IP, "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["command"] == "probe"
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 4
    assert payload["data"]["verdict"] == "findings"


def test_probe_json_inconclusive_is_a_typed_system_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.probe.collect_probe_result", lambda *_args, **_kwargs: _result("skipped"))

    result = runner.invoke(app, ["probe", _IP, "--json"])

    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(False)
    set_quiet_mode(False)
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["errors"][0]["code"] == "MERIDIAN_PROBE_INCONCLUSIVE"
    assert payload["data"]["verdict"] == "inconclusive"


def test_probe_help_documents_automation_controls() -> None:
    result = runner.invoke(app, ["probe", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--timeout" in result.output
    assert "--sni" in result.output
