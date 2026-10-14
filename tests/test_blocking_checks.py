"""SNI-vs-IP differential and data-stall verdicts from scripted TLS flow evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meridian.diagnostics.blocking import check_block_differential, check_data_stall
from meridian.diagnostics.tls_probe import TlsFlowResult
from tests.support.tls_server import self_signed_cert, tls_server

SERVER = "198.51.100.10"
REAL_SITE = "198.51.100.200"
SNI = "camouflage.example"


def _ok(ip: str, sni: str) -> TlsFlowResult:
    return TlsFlowResult(ip=ip, port=443, sni=sni, stage="complete", connect_ms=20, handshake_ms=60)


def _silenced(ip: str, sni: str, stage: str = "handshake", kind: str = "timeout") -> TlsFlowResult:
    return TlsFlowResult(ip=ip, port=443, sni=sni, stage=stage, error_kind=kind, connect_ms=20)  # type: ignore[arg-type]


class ScriptedProber:
    def __init__(self, answers: dict[tuple[str, str], TlsFlowResult]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def __call__(self, ip: str, sni: str = "", **_kwargs: Any) -> TlsFlowResult:
        self.calls.append((ip, sni))
        return self.answers[(ip, sni)]


def _resolver(_host: str, *, timeout: float) -> str:
    return REAL_SITE


def _differential(answers: dict[tuple[str, str], TlsFlowResult], resolver: Any = _resolver):
    prober = ScriptedProber(answers)
    check = check_block_differential(SERVER, SNI, timeout=1, prober=prober, resolver=resolver)
    return check, prober


def test_reachable_server_passes_after_one_handshake_and_states_the_vantage() -> None:
    check, prober = _differential({(SERVER, SNI): _ok(SERVER, SNI)})
    assert check.status == "passed"
    assert prober.calls == [(SERVER, SNI)]
    assert "inside the censored network" in check.findings[0].message


def test_sni_cut_off_while_no_sni_gets_through_is_a_blocked_camouflage_sni() -> None:
    check, _ = _differential(
        {
            (SERVER, SNI): _silenced(SERVER, SNI),
            (SERVER, ""): _ok(SERVER, ""),
            (REAL_SITE, SNI): _ok(REAL_SITE, SNI),
        }
    )
    assert check.status == "failed"
    assert check.findings[0].code == "CAMOUFLAGE_SNI_BLOCKED"
    assert "real site opens directly" in check.findings[0].message


def test_every_connection_silenced_while_real_site_opens_is_a_blocked_server_ip() -> None:
    check, _ = _differential(
        {
            (SERVER, SNI): _silenced(SERVER, SNI, stage="connect"),
            (SERVER, ""): _silenced(SERVER, "", stage="connect"),
            (REAL_SITE, SNI): _ok(REAL_SITE, SNI),
        }
    )
    assert check.status == "failed"
    assert check.findings[0].code == "SERVER_IP_BLOCKED"


def test_unreachable_real_camouflage_site_is_inconclusive_not_a_finding() -> None:
    check, _ = _differential(
        {
            (SERVER, SNI): _silenced(SERVER, SNI),
            (SERVER, ""): _silenced(SERVER, ""),
            (REAL_SITE, SNI): _silenced(REAL_SITE, SNI),
        }
    )
    assert check.status == "skipped"
    assert check.findings[0].code == "BLOCK_DIFFERENTIAL_INCONCLUSIVE"


def test_camouflage_dns_failure_is_inconclusive() -> None:
    check, _ = _differential(
        {(SERVER, SNI): _silenced(SERVER, SNI), (SERVER, ""): _silenced(SERVER, "")},
        resolver=lambda _host, *, timeout: None,
    )
    assert check.status == "skipped"


def test_refused_port_is_not_reported_as_censorship() -> None:
    check, prober = _differential({(SERVER, SNI): _silenced(SERVER, SNI, stage="connect", kind="refused")})
    assert check.status == "skipped"
    assert prober.calls == [(SERVER, SNI)]


def _stall_flow(**fields: Any) -> TlsFlowResult:
    base: dict[str, Any] = {"ip": SERVER, "port": 443, "sni": "", "connect_ms": 20, "handshake_ms": 60}
    return TlsFlowResult(**{**base, **fields})


def test_freeze_at_sixteen_kilobytes_warns_with_a_new_ip_remediation() -> None:
    flow = _stall_flow(stage="transfer", stalled=True, bytes_received=16 * 1024, error_kind="timeout")
    check = check_data_stall(SERVER, "/page/pwa/app.js", prober=lambda *_a, **_k: flow)
    assert check.status == "warning"
    assert "signature of throttling" in check.findings[0].message
    assert "new IP" in check.findings[0].remediation


def test_cdn_freeze_points_at_cloudflare_from_russia() -> None:
    flow = _stall_flow(stage="transfer", stalled=True, bytes_received=20 * 1024, error_kind="timeout")
    check = check_data_stall(SERVER, "/page/pwa/app.js", via_cdn=True, prober=lambda *_a, **_k: flow)
    assert check.status == "warning"
    assert "about 20 KB" in check.findings[0].remediation


def test_short_404_answer_is_too_small_to_judge() -> None:
    flow = _stall_flow(stage="complete", bytes_received=180, http_status=404, transfer_ms=80)
    check = check_data_stall(SERVER, "/page/pwa/app.js", prober=lambda *_a, **_k: flow)
    assert check.status == "skipped"
    assert "HTTP 404" in check.findings[0].message


def test_unreachable_server_leaves_data_flow_to_reachability_checks() -> None:
    flow = _stall_flow(stage="connect", error_kind="timeout", connect_ms=None, handshake_ms=None)
    check = check_data_stall(SERVER, "/page/pwa/app.js", prober=lambda *_a, **_k: flow)
    assert check.status == "skipped"


def test_request_path_and_host_are_sent_but_never_echoed() -> None:
    sent: dict[str, Any] = {}

    def prober(ip: str, sni: str = "", **kwargs: Any) -> TlsFlowResult:
        sent.update(kwargs, sni=sni)
        return _stall_flow(stage="complete", bytes_received=50 * 1024, transfer_ms=300, first_byte_ms=90)

    check = check_data_stall(SERVER, "/secret-page/pwa/app.js", server_name="vpn.example", prober=prober)
    assert check.status == "passed"
    assert b"GET /secret-page/pwa/app.js HTTP/1.1" in sent["request"]
    assert b"Host: vpn.example" in sent["request"]
    assert sent["sni"] == "vpn.example"
    assert "secret-page" not in check.findings[0].message


@pytest.fixture(scope="module")
def cert_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return self_signed_cert(tmp_path_factory.mktemp("tls"))


def test_real_socket_stall_after_sixteen_kilobytes_is_caught(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, behavior="stall", stall_after=16 * 1024) as port:
        check = check_data_stall("127.0.0.1", "/page/pwa/app.js", port=port, timeout=0.5)
    assert check.status == "warning"
    assert check.findings[0].code == "DATA_FLOW_STALLED"


def test_real_socket_full_asset_passes(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, body_bytes=50 * 1024) as port:
        check = check_data_stall("127.0.0.1", "/page/pwa/app.js", port=port, timeout=2)
    assert check.status == "passed"


def _job_names(jobs: list[Any]) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    return [(job.func.__name__, job.args, job.keywords) for job in jobs]


def test_control_server_with_wss_gets_differential_data_flow_and_cdn_jobs() -> None:
    from meridian.diagnostics.blocking import blocking_jobs
    from meridian.verification import DeploymentVerificationContext, VerificationHttpsRoute

    context = DeploymentVerificationContext(
        kind="v4",
        roles=("control", "exit:exit-a"),
        protocols=("reality", "wss"),
        reality_snis=(SNI,),
        reality_sni_ports={SNI: 443},
        public_tcp_ports=(443,),
        public_tls_ports=(443,),
        https_routes=(
            VerificationHttpsRoute(
                kind="managed", port=443, tls_sni="vpn.example", host_header="vpn.example", panel=True
            ),
        ),
        connection_page_path="page123",
    )
    jobs = _job_names(blocking_jobs(SERVER, context, timeout=2))
    assert [name for name, _args, _kw in jobs] == [
        "check_block_differential",
        "check_data_stall",
        "check_cdn_data_stall",
    ]
    assert jobs[1][1] == (SERVER, "/page123/pwa/app.js")
    assert jobs[1][2]["server_name"] == "vpn.example"
    assert jobs[2][1] == (SERVER, "vpn.example", "/page123/pwa/app.js")


def test_exit_only_server_has_no_meridian_asset_to_measure() -> None:
    from meridian.diagnostics.blocking import blocking_jobs
    from meridian.verification import DeploymentVerificationContext

    context = DeploymentVerificationContext(
        kind="v4",
        roles=("exit:exit-b",),
        protocols=("reality",),
        reality_snis=(SNI,),
        public_tcp_ports=(8443,),
        public_tls_ports=(8443,),
    )
    jobs = _job_names(blocking_jobs(SERVER, context))
    assert [name for name, _args, _kw in jobs] == ["check_block_differential"]
    assert jobs[0][2]["port"] == 8443


def test_external_target_runs_the_differential_only_for_an_explicit_sni() -> None:
    from meridian.diagnostics.blocking import blocking_jobs
    from meridian.verification import DeploymentVerificationContext

    context = DeploymentVerificationContext(endpoint_addresses=(SERVER,))
    assert blocking_jobs(SERVER, context) == []
    assert len(blocking_jobs(SERVER, context, requested_sni=SNI)) == 1


def test_cdn_hostname_pointing_at_the_server_has_no_cdn_in_the_path() -> None:
    from meridian.diagnostics.blocking import check_cdn_data_stall

    check = check_cdn_data_stall(SERVER, "vpn.example", "/p/pwa/app.js", resolver=lambda _h, *, timeout: SERVER)
    assert check.status == "passed"
    assert check.findings[0].code == "CDN_NOT_IN_PATH"


def test_cdn_edge_is_probed_with_the_hostname_as_sni() -> None:
    from meridian.diagnostics.blocking import check_cdn_data_stall

    seen: list[tuple[str, str]] = []

    def prober(ip: str, sni: str = "", **_kwargs: Any) -> TlsFlowResult:
        seen.append((ip, sni))
        return _stall_flow(ip=ip, stage="transfer", stalled=True, bytes_received=19 * 1024)

    check = check_cdn_data_stall(
        SERVER, "vpn.example", "/p/pwa/app.js", resolver=lambda _h, *, timeout: "203.0.113.50", prober=prober
    )
    assert seen == [("203.0.113.50", "vpn.example")]
    assert check.id == "cdn_data_stall"
    assert check.status == "warning"
