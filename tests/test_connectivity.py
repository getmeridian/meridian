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
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.core.verification import VerificationFinding, aggregate_verification, build_verification_check
from meridian.remnawave import RemnawaveAuthError, RemnawaveError, RemnawaveNetworkError, SubscriptionDocument, User
from meridian.verification import DeploymentVerificationContext, VerificationHttpsRoute

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
    intent = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["preferred", "fallback"]),
    )
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
    intent = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["alice"]),
    )
    panel = FakePanel(
        [
            User(username="alice", status="DISABLED", short_uuid="short-alice"),
            User(username="meridian-route-123", status="ACTIVE", short_uuid="short-service"),
        ]
    )

    selected = select_active_client(panel, ClusterConfig(topology_intent=intent), "")  # type: ignore[arg-type]

    assert selected is None


def test_v4_requested_client_selection_rejects_service_user_without_lookup() -> None:
    intent = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["alice"]),
    )
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
    panel = FakePanel([User(username="alice", status="ACTIVE", short_uuid="short-alice")])
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
    ) -> tuple[bool, str]:
        assert timeout == 7
        calls.append((label, config, server_ip, expect_ip_match))
        return True, "exit IP 203.0.113.10"

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        timeout=7,
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda *, timeout: provider_timeouts.append(timeout) or tmp_path / "xray",
        connection_tester=execute,
    )

    assert selected == "alice"
    assert provider_timeouts == [120]
    assert [check.status for check in checks] == ["passed", "passed", "passed"]
    assert [call[0] for call in calls] == ["Automatic fallback", f"Hysteria2 {_IP}"]
    assert calls[0][3] is False
    assert calls[1][2:] == (_IP, True)
    pinned_hysteria = calls[1][1]["outbounds"][1]
    assert pinned_hysteria == _canonical_config()["outbounds"][1]
    assert calls[1][1]["routing"]["rules"][-1]["outboundTag"] == "MERIDIAN_PROXY_HY2"


def test_inactive_requested_client_is_a_real_finding() -> None:
    panel = FakePanel([User(username="alice", status="DISABLED", short_uuid="short-alice")])

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda **_kwargs: None,
        connection_tester=lambda *_args: (True, "unused"),
    )

    assert selected == ""
    assert checks[0].status == "failed"
    assert checks[0].findings[0].code == "ACTIVE_CLIENT_MISSING"


def test_panel_network_error_is_inconclusive_not_a_dummy_auth_test() -> None:
    panel = FakePanel(
        [User(username="alice", status="ACTIVE", short_uuid="short-alice")],
        error=RemnawaveNetworkError("offline"),
    )

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda **_kwargs: None,
        connection_tester=lambda *_args: (True, "unused"),
    )

    assert selected == "alice"
    assert checks[0].status == "skipped"
    assert checks[0].findings[0].code == "PANEL_UNAVAILABLE"
    assert aggregate_verification(checks).exit_code == 3


def test_panel_system_error_is_inconclusive_not_a_connectivity_failure() -> None:
    panel = FakePanel(
        [User(username="alice", status="ACTIVE", short_uuid="short-alice")],
        error=RemnawaveError("Subscription fetch failed (503)", category="system"),
    )

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda **_kwargs: None,
        connection_tester=lambda *_args: (True, "unused"),
    )

    assert selected == "alice"
    assert checks[0].status == "skipped"
    assert checks[0].findings[0].code == "PANEL_UNAVAILABLE"
    assert aggregate_verification(checks).exit_code == 3


def test_panel_bad_request_is_a_completed_connectivity_finding() -> None:
    panel = FakePanel(
        [User(username="alice", status="ACTIVE", short_uuid="short-alice")],
        error=RemnawaveError("Subscription fetch failed (422)", category="user"),
    )

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda **_kwargs: None,
        connection_tester=lambda *_args: (True, "unused"),
    )

    assert selected == "alice"
    assert checks[0].status == "failed"
    assert checks[0].findings[0].code == "PANEL_REQUEST_FAILED"
    assert aggregate_verification(checks).exit_code == 4


def test_panel_auth_error_remains_a_typed_user_error() -> None:
    panel = FakePanel(
        [User(username="alice", status="ACTIVE", short_uuid="short-alice")],
        error=RemnawaveAuthError("token expired", category="user"),
    )

    with pytest.raises(RemnawaveAuthError, match="token expired"):
        check_canonical_subscription(
            _cluster(),
            _context(),
            _IP,
            requested_client="alice",
            panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
            xray_provider=lambda **_kwargs: None,
            connection_tester=lambda *_args: (True, "unused"),
        )


def test_invalid_local_inbound_is_a_failed_protocol_check(tmp_path: Path) -> None:
    config = _canonical_config()
    config["inbounds"].append({"protocol": "dokodemo-door", "listen": "0.0.0.0", "port": 9999})
    tester = patch("meridian.connectivity.test_connection")

    with tester as connection:
        result = execute_xray_config(
            "protocol",
            "Reality",
            config,
            tmp_path / "xray",
            _IP,
            connection,
            expected_egress_ip=_IP,
            timeout=5,
        )

    assert result.status == "failed"
    assert result.findings[0].code == "PROTOCOL_CONFIG_INVALID"
    connection.assert_not_called()


def test_unavailable_ip_observers_make_protocol_check_inconclusive(tmp_path: Path) -> None:
    result = execute_xray_config(
        "protocol",
        "Reality",
        _canonical_config(),
        tmp_path / "xray",
        _IP,
        lambda *_args, **_kwargs: (None, "IP observers unavailable"),
        expected_egress_ip=_IP,
        timeout=5,
    )

    assert result.status == "skipped"
    assert result.findings[0].code == "PROTOCOL_TRAFFIC_INCONCLUSIVE"


def test_invalid_canonical_routing_is_a_failed_protocol_check(tmp_path: Path) -> None:
    config = _canonical_config()
    config["routing"] = []

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
            outbound_tag="MERIDIAN_PROXY_VLESS",
        )

    assert result.status == "failed"
    assert result.findings[0].code == "PROTOCOL_CONFIG_INVALID"
    connection.assert_not_called()


def test_missing_xray_runtime_is_inconclusive() -> None:
    panel = FakePanel([User(username="alice", status="ACTIVE", short_uuid="short-alice")])

    checks, selected = check_canonical_subscription(
        _cluster(),
        _context(),
        _IP,
        requested_client="alice",
        panel_factory=lambda *_args: panel,  # type: ignore[arg-type]
        xray_provider=lambda **_kwargs: None,
        connection_tester=lambda *_args: (True, "unused"),
    )

    assert selected == "alice"
    assert checks[-1].status == "skipped"
    assert checks[-1].findings[0].code == "XRAY_UNAVAILABLE"
