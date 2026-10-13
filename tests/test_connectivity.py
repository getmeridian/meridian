"""Canonical end-to-end connectivity verification."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.connectivity import (
    check_canonical_subscription,
    check_device_clock,
    check_tcp_reachability,
    collect_connectivity_checks,
    execute_xray_config,
    select_active_client,
)
from meridian.core.verification import VerificationFinding, aggregate_verification, build_verification_check
from meridian.remnawave import RemnawaveAuthError, RemnawaveError, RemnawaveNetworkError, SubscriptionDocument, User
from meridian.verification import DeploymentVerificationContext, VerificationHttpsRoute
from meridian.xray_client import ConnectionAttempt
from tests.support.topology import single_exit_intent


def _alice(status: str = "ACTIVE", **panel_kwargs) -> FakePanel:
    return FakePanel([User(username="alice", status=status, short_uuid="short-alice")], **panel_kwargs)


def _canonical(panel: FakePanel, **overrides):
    """check_canonical_subscription for client alice with no local Xray unless overridden."""
    kwargs = {
        "requested_client": "alice",
        "panel_factory": lambda *_args: panel,
        "xray_provider": lambda **_kwargs: None,
        "connection_tester": lambda *_args: ConnectionAttempt(True, "unused"),
        **overrides,
    }
    return check_canonical_subscription(_cluster(), _context(), _IP, **kwargs)


_IP = "198.51.100.20"


def _canonical_config() -> dict:
    return {
        "inbounds": [{"tag": "SOCKS", "protocol": "socks", "port": 10808}],
        "outbounds": [
            {
                "tag": "MERIDIAN_PROXY_VLESS",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "198.51.100.10",
                            "port": 443,
                            "users": [{"id": "11111111-1111-1111-1111-111111111111"}],
                        }
                    ]
                },
            },
            {
                "tag": "MERIDIAN_PROXY_HY2",
                "protocol": "hysteria",
                "settings": {"version": 2, "address": _IP, "port": 443},
                "streamSettings": {"network": "hysteria", "hysteriaSettings": {"version": 2}},
            },
        ],
        "routing": {"rules": [], "balancers": [{"tag": "AUTO", "selector": ["MERIDIAN_PROXY"]}]},
    }


class FakePanel:
    def __init__(self, users: list[User], content: str | None = None, error: Exception | None = None) -> None:
        self.users = users
        self.content = content or json.dumps(_canonical_config())
        self.error = error

    def __enter__(self) -> FakePanel:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get_user(self, username: str) -> User | None:
        return next((user for user in self.users if user.username == username), None)

    def list_users(self) -> list[User]:
        return list(self.users)

    def fetch_subscription(self, short_uuid: str, *, client_type: str = "") -> SubscriptionDocument:
        if self.error:
            raise self.error
        return SubscriptionDocument(
            url=f"https://panel.example/api/sub/{short_uuid}/{client_type}",
            content=self.content,
            client_type=client_type,
        )


def _cluster() -> ClusterConfig:
    return ClusterConfig(panel=PanelConfig(url="https://panel.example", api_token="token"))


def _context() -> DeploymentVerificationContext:
    return DeploymentVerificationContext(
        kind="v4",
        roles=("exit:exit-a",),
        protocols=("reality", "hysteria2"),
        public_tcp_ports=(443,),
        public_udp_ports=(443,),
        endpoint_addresses=(_IP,),
        expected_egress_ip=_IP,
    )


def test_clock_uses_target_date_not_a_third_party() -> None:
    with patch(
        "meridian.connectivity.https_get",
        return_value=(403, {"date": "Tue, 21 Jul 2026 12:00:00 GMT"}, b""),
    ) as request:
        result = check_device_clock(
            _IP,
            server_name="www.example.com",
            now=lambda: 1784635210,
        )

    assert result.status == "passed"
    assert "10 seconds" in result.findings[0].message
    assert request.call_args.kwargs["server_name"] == "www.example.com"


def test_clock_drift_past_a_minute_is_a_warning_not_a_failure() -> None:
    with patch(
        "meridian.connectivity.https_get",
        return_value=(403, {"date": "Tue, 21 Jul 2026 12:00:00 GMT"}, b""),
    ):
        result = check_device_clock(_IP, server_name=_IP, now=lambda: 1784635200 + 90)

    assert result.status == "warning"
    assert result.findings[0].code == "CLOCK_DRIFT"
    assert "automatic date and time" in result.findings[0].remediation


def test_missing_clock_evidence_is_inconclusive() -> None:
    with patch("meridian.connectivity.https_get", return_value=(0, {}, b"")):
        result = check_device_clock(_IP, server_name=_IP)

    assert result.status == "skipped"


def test_tcp_reachability_fails_when_any_required_listener_is_closed() -> None:
    with patch("meridian.connectivity.tcp_connect", side_effect=lambda _ip, port, timeout=5: port == 443):
        result = check_tcp_reachability(_IP, (443, 8443))

    assert result.status == "failed"
    assert any(finding.code == "TCP_8443_UNREACHABLE" for finding in result.findings)


def test_hysteria_only_basic_test_requires_full_protocol_execution() -> None:
    result = collect_connectivity_checks(
        ClusterConfig(),
        DeploymentVerificationContext(
            kind="v4",
            protocols=("hysteria2",),
            public_tcp_ports=(),
            public_udp_ports=(443,),
            endpoint_addresses=(_IP,),
        ),
        _IP,
        basic=True,
    )

    assert [check.id for check in result.checks] == ["udp_reachability"]
    assert result.checks[0].status == "skipped"
    assert result.checks[0].findings[0].code == "UDP_FULL_TEST_REQUIRED"


def test_basic_test_runs_tls_checks_on_configured_nonstandard_port() -> None:
    passed = build_verification_check(
        "passed",
        "Passed",
        [VerificationFinding(code="PASSED", status="passed", message="passed")],
    )
    context = DeploymentVerificationContext(
        kind="v4",
        protocols=("reality",),
        reality_snis=("www.example.com",),
        public_tcp_ports=(8443,),
        public_tls_ports=(8443,),
        endpoint_addresses=(_IP,),
    )
    with (
        patch("meridian.connectivity.check_device_clock", return_value=passed) as clock,
        patch("meridian.connectivity.check_tcp_reachability", return_value=passed),
        patch("meridian.connectivity.check_tls_certificate", return_value=passed),
        patch("meridian.connectivity.check_sni_camouflage", return_value=passed) as camouflage,
    ):
        result = collect_connectivity_checks(ClusterConfig(), context, _IP, basic=True)

    assert result.checks
    assert clock.call_args.kwargs["port"] == 8443
    assert camouflage.call_args.kwargs["port"] == 8443


def test_basic_test_checks_managed_certificate_alongside_reality() -> None:
    passed = build_verification_check(
        "passed",
        "Passed",
        [VerificationFinding(code="PASSED", status="passed", message="passed")],
    )
    context = DeploymentVerificationContext(
        kind="v4",
        protocols=("reality", "xhttp"),
        public_tcp_ports=(443,),
        public_tls_ports=(443,),
        https_routes=(
            VerificationHttpsRoute(kind="camouflage", port=443, tls_sni="www.example.com"),
            VerificationHttpsRoute(
                kind="managed",
                port=443,
                tls_sni="tls.example.com",
                host_header="host.example.com",
            ),
        ),
    )
    with (
        patch("meridian.connectivity.check_device_clock", return_value=passed),
        patch("meridian.connectivity.check_tcp_reachability", return_value=passed),
        patch("meridian.connectivity.check_sni_camouflage", return_value=passed) as camouflage,
        patch("meridian.connectivity.check_tls_certificate", return_value=passed) as certificate,
        patch("meridian.connectivity.check_domain_root", return_value=passed) as domain_root,
    ):
        collect_connectivity_checks(ClusterConfig(), context, _IP, basic=True)

    camouflage.assert_called_once_with(_IP, "www.example.com", port=443, timeout=5)
    assert {call.kwargs["server_name"] for call in certificate.call_args_list} == {"www.example.com", "tls.example.com"}
    domain_root.assert_called_once_with(
        _IP,
        "host.example.com",
        server_name="tls.example.com",
        port=443,
        timeout=5,
    )


def test_client_selection_prefers_v4_access_order() -> None:
    intent = single_exit_intent(users=["preferred", "fallback"])
    panel = FakePanel(
        [
            User(username="fallback", status="ACTIVE", short_uuid="short-fallback"),
            User(username="preferred", status="ACTIVE", short_uuid="short-preferred"),
        ]
    )

    selected = select_active_client(panel, ClusterConfig(topology_intent=intent), "")  # type: ignore[arg-type]

    assert selected is not None
    assert selected.username == "preferred"


def test_v4_default_client_selection_never_falls_back_to_service_users() -> None:
    intent = single_exit_intent(users=["alice"])
    panel = FakePanel(
        [
            User(username="alice", status="DISABLED", short_uuid="short-alice"),
            User(username="meridian-route-123", status="ACTIVE", short_uuid="short-service"),
        ]
    )

    selected = select_active_client(panel, ClusterConfig(topology_intent=intent), "")  # type: ignore[arg-type]

    assert selected is None


def test_v4_requested_client_selection_rejects_service_user_without_lookup() -> None:
    intent = single_exit_intent(users=["alice"])
    panel = FakePanel([User(username="meridian-route-123", status="ACTIVE", short_uuid="short-service")])
    with patch.object(panel, "get_user", wraps=panel.get_user) as get_user:
        selected = select_active_client(
            panel,  # type: ignore[arg-type]
            ClusterConfig(topology_intent=intent),
            "meridian-route-123",
        )

    assert selected is None
    get_user.assert_not_called()


def test_legacy_default_client_selection_still_uses_panel_users() -> None:
    panel = FakePanel(
        [
            User(username="zeta", status="ACTIVE", short_uuid="short-zeta"),
            User(username="alpha", status="ACTIVE", short_uuid="short-alpha"),
        ]
    )

    selected = select_active_client(panel, ClusterConfig(), "")  # type: ignore[arg-type]

    assert selected is not None
    assert selected.username == "alpha"


def test_full_test_executes_fallback_and_target_hysteria_without_rebuilding(tmp_path: Path) -> None:
    calls: list[tuple[str, dict, str, bool]] = []
    provider_timeouts: list[float] = []

    def execute(
        _binary: Path,
        config: dict,
        server_ip: str,
        _socks_port: int,
        label: str,
        expect_ip_match: bool,
        *,
        timeout: float,
    ) -> ConnectionAttempt:
        assert timeout == 7
        calls.append((label, config, server_ip, expect_ip_match))
        return ConnectionAttempt(True, "exit IP 203.0.113.10", 120)

    run = _canonical(
        _alice(),
        timeout=7,
        xray_provider=lambda *, timeout: provider_timeouts.append(timeout) or tmp_path / "xray",
        connection_tester=execute,
    )

    assert run.client == "alice"
    assert provider_timeouts == [120]
    assert [check.status for check in run.checks] == ["passed", "passed", "passed"]
    assert [call[0] for call in calls] == ["Automatic fallback", f"Hysteria2 · UDP {_IP}"]
    assert calls[0][3] is False
    assert calls[1][2:] == (_IP, True)
    pinned_hysteria = calls[1][1]["outbounds"][1]
    assert pinned_hysteria == _canonical_config()["outbounds"][1]
    assert calls[1][1]["routing"]["rules"][-1]["outboundTag"] == "MERIDIAN_PROXY_HY2"


def test_inactive_requested_client_is_a_real_finding() -> None:
    run = _canonical(_alice("DISABLED"))

    assert run.client == ""
    assert run.checks[0].status == "failed"
    assert run.checks[0].findings[0].code == "ACTIVE_CLIENT_MISSING"


@pytest.mark.parametrize(
    ("error", "status", "code", "exit_code"),
    [
        # Panel trouble is unavailable evidence, never a dummy auth or connectivity failure.
        (RemnawaveNetworkError("offline"), "skipped", "PANEL_UNAVAILABLE", 3),
        (RemnawaveError("Subscription fetch failed (503)", category="system"), "skipped", "PANEL_UNAVAILABLE", 3),
        (RemnawaveError("Subscription fetch failed (422)", category="user"), "failed", "PANEL_REQUEST_FAILED", 4),
    ],
    ids=["network", "system", "bad-request"],
)
def test_panel_errors_classify_the_subscription_check(error, status: str, code: str, exit_code: int) -> None:
    run = _canonical(_alice(error=error))

    assert run.client == "alice"
    assert run.checks[0].status == status
    assert run.checks[0].findings[0].code == code
    assert aggregate_verification(run.checks).exit_code == exit_code


def test_panel_auth_error_remains_a_typed_user_error() -> None:
    with pytest.raises(RemnawaveAuthError, match="token expired"):
        _canonical(_alice(error=RemnawaveAuthError("token expired", category="user")))


def _with_open_inbound(config: dict) -> None:
    config["inbounds"].append({"protocol": "dokodemo-door", "listen": "0.0.0.0", "port": 9999})


def _without_routing(config: dict) -> None:
    config["routing"] = []


@pytest.mark.parametrize(
    ("mutate", "outbound_tag"),
    [(_with_open_inbound, None), (_without_routing, "MERIDIAN_PROXY_VLESS")],
    ids=["open-local-inbound", "invalid-routing"],
)
def test_invalid_canonical_config_fails_before_any_traffic(tmp_path: Path, mutate, outbound_tag) -> None:
    config = _canonical_config()
    mutate(config)
    extra = {"outbound_tag": outbound_tag} if outbound_tag else {}

    with patch("meridian.connectivity.test_connection") as connection:
        result = execute_xray_config(
            "protocol",
            "Reality",
            config,
            tmp_path / "xray",
            _IP,
            connection,
            expected_egress_ip=_IP,
            timeout=5,
            **extra,
        )

    assert result.check.status == "failed"
    assert result.check.findings[0].code == "PROTOCOL_CONFIG_INVALID"
    connection.assert_not_called()


@pytest.mark.parametrize(
    ("attempt", "status", "code"),
    [
        (ConnectionAttempt(None, "IP observers unavailable"), "skipped", "PROTOCOL_TRAFFIC_INCONCLUSIVE"),
        (
            ConnectionAttempt(False, f"exit IP 203.0.113.9 does not match server {_IP}"),
            "failed",
            "PROTOCOL_EGRESS_MISMATCH",
        ),
    ],
    ids=["observers-unavailable", "egress-mismatch"],
)
def test_traffic_outcome_classifies_the_protocol_check(tmp_path: Path, attempt, status: str, code: str) -> None:
    result = execute_xray_config(
        "protocol",
        "Reality",
        _canonical_config(),
        tmp_path / "xray",
        _IP,
        lambda *_args, **_kwargs: attempt,
        expected_egress_ip=_IP,
        timeout=5,
    )

    assert result.check.status == status
    assert result.check.findings[0].code == code


def test_missing_xray_runtime_is_inconclusive() -> None:
    run = _canonical(_alice())

    assert run.client == "alice"
    assert run.checks[-1].status == "skipped"
    assert run.checks[-1].findings[0].code == "XRAY_UNAVAILABLE"


def _failed_attempt(*_args, **_kwargs) -> ConnectionAttempt:
    return ConnectionAttempt(False, "curl failed (connection reset) after 3 attempts; observer works directly")


def test_failed_traffic_with_an_open_port_is_connects_but_no_traffic(tmp_path: Path) -> None:
    probes: list[tuple[str, int, float]] = []
    run = execute_xray_config(
        "protocol_1",
        f"VLESS {_IP}",
        _canonical_config(),
        tmp_path / "xray",
        _IP,
        _failed_attempt,
        expected_egress_ip=_IP,
        timeout=8,
        endpoint=(_IP, 443),
        endpoint_probe=lambda host, port, timeout: probes.append((host, port, timeout)) or True,
    )

    assert run.check.findings[0].code == "PROTOCOL_NO_TRAFFIC"
    assert "connects to port 443, but no traffic passes" in run.check.findings[0].message
    assert run.transport.outcome == "no_traffic"
    assert probes == [(_IP, 443, 5.0)]


def test_failed_traffic_with_a_closed_port_is_unreachable(tmp_path: Path) -> None:
    run = execute_xray_config(
        "protocol_1",
        f"VLESS {_IP}",
        _canonical_config(),
        tmp_path / "xray",
        _IP,
        _failed_attempt,
        expected_egress_ip=_IP,
        timeout=5,
        endpoint=(_IP, 443),
        endpoint_probe=lambda *_args: False,
    )

    assert run.check.status == "failed"
    assert run.check.findings[0].code == "PROTOCOL_UNREACHABLE"
    assert run.transport.outcome == "unreachable"


def test_udp_transport_failure_stays_a_plain_failure_without_a_tcp_probe(tmp_path: Path) -> None:
    run = execute_xray_config(
        "protocol_1",
        f"Hysteria2 {_IP}",
        _canonical_config(),
        tmp_path / "xray",
        _IP,
        _failed_attempt,
        expected_egress_ip=_IP,
        timeout=5,
        endpoint_probe=lambda *_args: pytest.fail("UDP endpoints are never TCP-probed"),
    )

    assert run.check.findings[0].code == "PROTOCOL_TRAFFIC_FAILED"
    assert run.transport.outcome == "failed"


def test_full_test_reports_each_transport_with_time_to_first_byte(tmp_path: Path) -> None:
    run = _canonical(
        _alice(),
        xray_provider=lambda **_kwargs: tmp_path / "xray",
        connection_tester=lambda *_args, **_kwargs: ConnectionAttempt(True, "exit IP ok", 230),
    )

    assert [(item.check_id, item.outcome, item.first_byte_ms) for item in run.transports] == [("protocol_1", "ok", 230)]
    assert run.transports[0].label == f"Hysteria2 · UDP {_IP}"
