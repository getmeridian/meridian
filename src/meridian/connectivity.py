"""Command-free client-side proxy connectivity verification."""

from __future__ import annotations

import email.utils
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from meridian.cluster import ClusterConfig
from meridian.core.verification import (
    VerificationCheck,
    VerificationFinding,
    build_verification_check,
)
from meridian.diagnostics.external import (
    check_domain_root,
    check_sni_camouflage,
    check_tls_certificate,
    https_get,
)
from meridian.health import tcp_connect
from meridian.remnawave import (
    XRAY_JSON_CLIENT_TYPE,
    MeridianPanel,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    User,
)
from meridian.verification import (
    DeploymentVerificationContext,
    VerificationHttpsRoute,
    canonical_endpoint_address,
)
from meridian.xray_client import (
    XrayStartupError,
    ensure_xray_binary,
    is_supported_proxy_outbound,
    outbound_address,
    outbound_label,
    parse_xray_subscription,
    proxy_outbounds,
    route_only,
    test_connection,
    use_free_local_ports,
)

PanelFactory = Callable[[str, str, float], MeridianPanel]


class XrayProvider(Protocol):
    def __call__(self, *, timeout: float) -> Path | None: ...


class ConnectionTester(Protocol):
    def __call__(
        self,
        xray_bin: Path,
        config: dict[str, Any],
        server_ip: str,
        socks_port: int,
        label: str,
        expect_ip_match: bool,
        *,
        timeout: float,
    ) -> tuple[bool | None, str]: ...


def create_verification_panel(url: str, token: str, timeout: float) -> MeridianPanel:
    """Create a single-attempt panel client bounded by the verification timeout."""
    return MeridianPanel(url, token, timeout=max(1, math.ceil(timeout)), max_retries=1)


@dataclass(frozen=True)
class ConnectivityChecks:
    """Checks plus the non-secret client identity used for full verification."""

    checks: list[VerificationCheck]
    client: str = ""


def _connectivity_routes(
    deployment: DeploymentVerificationContext,
    *,
    domain: str,
    sni: str,
) -> tuple[VerificationHttpsRoute, ...]:
    routes = list(deployment.https_routes)
    if domain:
        matches = [route for route in routes if route.host_header == domain and (not sni or route.tls_sni == sni)]
        if not matches:
            port = deployment.public_tls_ports[0] if deployment.public_tls_ports else 443
            routes.append(
                VerificationHttpsRoute(
                    kind="generic" if deployment.kind == "external" else "managed",
                    port=port,
                    tls_sni=sni or domain,
                    host_header=domain,
                )
            )
    elif sni and not any(route.tls_sni == sni for route in routes):
        port = deployment.public_tls_ports[0] if deployment.public_tls_ports else 443
        routes.append(
            VerificationHttpsRoute(
                kind="generic" if deployment.kind == "external" else "camouflage",
                port=port,
                tls_sni=sni,
            )
        )
    named_ports = {route.port for route in routes if route.tls_sni or route.host_header}
    routes = [
        route
        for route in routes
        if route.kind != "generic" or route.tls_sni or route.host_header or route.port not in named_ports
    ]
    return tuple(sorted(set(routes), key=lambda route: (route.port, route.kind, route.tls_sni, route.host_header)))


def _qualify_route_check(
    check: VerificationCheck,
    route: VerificationHttpsRoute,
    *,
    qualify: bool,
) -> VerificationCheck:
    if not qualify:
        return check
    tls_identity = route.tls_sni or "ip"
    host_identity = route.host_header or tls_identity
    suffix = f"{route.kind}:{tls_identity}:{host_identity}:{route.port}"
    return check.model_copy(update={"id": f"{check.id}:{suffix}", "name": f"{check.name} ({suffix})"})


def collect_connectivity_checks(
    cluster: ClusterConfig,
    deployment: DeploymentVerificationContext,
    target_ip: str,
    *,
    domain: str = "",
    sni: str = "",
    client: str = "",
    basic: bool = False,
    timeout: float = 5,
    panel_factory: PanelFactory = create_verification_panel,
    xray_provider: XrayProvider = ensure_xray_binary,
    connection_tester: ConnectionTester = test_connection,
) -> ConnectivityChecks:
    """Verify basic reachability and, unless basic, canonical client traffic."""
    routes = _connectivity_routes(deployment, domain=domain, sni=sni)
    checks: list[VerificationCheck] = []
    if routes:
        clock_route = min(routes, key=lambda route: (route.kind != "managed", route.port))
        checks.append(
            check_device_clock(
                target_ip,
                server_name=clock_route.tls_sni or target_ip,
                host_header=clock_route.host_header,
                port=clock_route.port,
                timeout=timeout,
            )
        )
    if deployment.public_tcp_ports:
        checks.append(check_tcp_reachability(target_ip, deployment.public_tcp_ports, timeout=timeout))
    qualify_ids = len(routes) > 1
    for route in routes:
        tls_sni = route.tls_sni or target_ip
        if route.kind == "camouflage" and route.tls_sni:
            certificate = check_tls_certificate(
                target_ip,
                server_name=tls_sni,
                port=route.port,
                timeout=timeout,
            )
            checks.append(_qualify_route_check(certificate, route, qualify=qualify_ids))
            check = check_sni_camouflage(target_ip, route.tls_sni, port=route.port, timeout=timeout)
        else:
            check = check_tls_certificate(
                target_ip,
                server_name=tls_sni,
                port=route.port,
                timeout=timeout,
            )
        checks.append(_qualify_route_check(check, route, qualify=qualify_ids))
        if route.kind == "managed":
            check = check_domain_root(
                target_ip,
                route.host_header or tls_sni,
                server_name=tls_sni,
                port=route.port,
                timeout=timeout,
            )
            checks.append(_qualify_route_check(check, route, qualify=qualify_ids))
    if basic:
        if deployment.public_udp_ports:
            checks.append(check_udp_requires_full_test(deployment.public_udp_ports))
        return ConnectivityChecks(checks=checks)

    protocol_checks, selected_client = check_canonical_subscription(
        cluster,
        deployment,
        target_ip,
        requested_client=client,
        timeout=timeout,
        panel_factory=panel_factory,
        xray_provider=xray_provider,
        connection_tester=connection_tester,
    )
    checks.extend(protocol_checks)
    return ConnectivityChecks(checks=checks, client=selected_client)


def check_device_clock(
    ip: str,
    *,
    server_name: str,
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
    now: Callable[[], float] = time.time,
) -> VerificationCheck:
    """Compare the local clock with the target's HTTPS Date header."""
    started = time.monotonic()
    status, headers, _ = https_get(
        ip,
        "/",
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
    )
    date_header = headers.get("date", "")
    if status == 0 or not date_header:
        findings = [
            VerificationFinding(
                code="CLOCK_EVIDENCE_UNAVAILABLE",
                status="skipped",
                message="Target did not provide an HTTPS Date header",
                remediation="Enable automatic time synchronization on this device and retry.",
            )
        ]
    else:
        try:
            parsed = email.utils.parsedate_to_datetime(date_header)
            reference = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            findings = [
                VerificationFinding(
                    code="CLOCK_DATE_INVALID",
                    status="skipped",
                    message="Target returned an invalid HTTPS Date header",
                )
            ]
        else:
            drift = abs(int(now() - reference))
            if drift > 30:
                findings = [
                    VerificationFinding(
                        code="CLOCK_DRIFT",
                        status="failed",
                        message=f"Device clock differs from the target by {drift} seconds",
                        remediation="Enable automatic date and time before using Reality.",
                    )
                ]
            else:
                findings = [
                    VerificationFinding(
                        code="CLOCK_OK",
                        status="passed",
                        message=f"Device clock is within {drift} seconds of the target",
                    )
                ]
    return build_verification_check(
        "device_clock",
        "Device clock",
        findings,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


def check_tcp_reachability(
    ip: str,
    ports: tuple[int, ...],
    *,
    timeout: float = 5,
) -> VerificationCheck:
    """Require each configured public TCP listener to be reachable."""
    started = time.monotonic()
    expected = tuple(sorted(set(ports)))
    with ThreadPoolExecutor(max_workers=len(expected)) as executor:
        observations = dict(
            zip(expected, executor.map(lambda port: tcp_connect(ip, port, timeout=timeout), expected), strict=True)
        )
    findings = []
    for port, reachable in observations.items():
        findings.append(
            VerificationFinding(
                code=f"TCP_{port}_{'REACHABLE' if reachable else 'UNREACHABLE'}",
                status="passed" if reachable else "failed",
                message=f"TCP/{port} is {'reachable' if reachable else 'not reachable'}",
                remediation=(
                    "Check the service, cloud firewall, local firewall, and possible network blocking."
                    if not reachable
                    else ""
                ),
            )
        )
    return build_verification_check(
        "tcp_reachability",
        "TCP reachability",
        findings,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


def check_udp_requires_full_test(ports: tuple[int, ...]) -> VerificationCheck:
    """Keep basic mode honest when only a real protocol handshake can verify UDP."""
    rendered = ", ".join(f"UDP/{port}" for port in sorted(set(ports)))
    return build_verification_check(
        "udp_reachability",
        "UDP reachability",
        [
            VerificationFinding(
                code="UDP_FULL_TEST_REQUIRED",
                status="skipped",
                message=f"Configured {rendered} was not tested in basic mode",
                remediation="Run `meridian test` without --basic to execute the canonical subscription.",
            )
        ],
    )


def check_canonical_subscription(
    cluster: ClusterConfig,
    deployment: DeploymentVerificationContext,
    target_ip: str,
    *,
    requested_client: str,
    timeout: float = 15,
    panel_factory: PanelFactory,
    xray_provider: XrayProvider,
    connection_tester: ConnectionTester,
) -> tuple[list[VerificationCheck], str]:
    """Fetch and execute the exact Xray document delivered to one active user."""
    if not cluster.panel.url or not cluster.panel.api_token:
        return [
            build_verification_check(
                "canonical_subscription",
                "Canonical subscription",
                [
                    VerificationFinding(
                        code="PANEL_CREDENTIALS_MISSING",
                        status="skipped",
                        message="Saved panel credentials are unavailable; full proxy verification did not run",
                        remediation="Run `meridian test --basic` intentionally or restore cluster panel credentials.",
                    )
                ],
            )
        ], ""

    selected: User | None = None
    try:
        with panel_factory(cluster.panel.url, cluster.panel.api_token, timeout) as panel:
            selected = select_active_client(panel, cluster, requested_client)
            if selected is None:
                message = (
                    f"Client {requested_client!r} is missing, inactive, or has no subscription"
                    if requested_client
                    else "No active client with a canonical subscription is available"
                )
                return [
                    build_verification_check(
                        "canonical_subscription",
                        "Canonical subscription",
                        [
                            VerificationFinding(
                                code="ACTIVE_CLIENT_MISSING",
                                status="failed",
                                message=message,
                                remediation="Create or enable a client, then retry with --client NAME.",
                            )
                        ],
                    )
                ], ""
            document = panel.fetch_subscription(selected.short_uuid, client_type=XRAY_JSON_CLIENT_TYPE)
    except RemnawaveAuthError:
        raise
    except RemnawaveError as exc:
        unavailable = isinstance(exc, RemnawaveNetworkError) or exc.category == "system"
        return [
            build_verification_check(
                "canonical_subscription",
                "Canonical subscription",
                [
                    VerificationFinding(
                        code="PANEL_UNAVAILABLE" if unavailable else "PANEL_REQUEST_FAILED",
                        status="skipped" if unavailable else "failed",
                        message=(
                            "Panel could not provide the canonical subscription"
                            if unavailable
                            else "Panel rejected the canonical subscription request"
                        ),
                        remediation="Check panel credentials and reachability, then retry.",
                    )
                ],
            )
        ], selected.username if selected is not None else ""

    try:
        config = parse_xray_subscription(document.content)
    except ValueError as exc:
        return [
            build_verification_check(
                "canonical_subscription",
                "Canonical subscription",
                [
                    VerificationFinding(
                        code="SUBSCRIPTION_INVALID",
                        status="failed",
                        message=str(exc),
                        remediation="Repair the Remnawave subscription template and host bindings.",
                    )
                ],
            )
        ], selected.username

    checks = [
        build_verification_check(
            "canonical_subscription",
            "Canonical subscription",
            [
                VerificationFinding(
                    code="SUBSCRIPTION_VALID",
                    status="passed",
                    message=f"Canonical Xray subscription loaded for client {selected.username}",
                )
            ],
        )
    ]
    delivered = proxy_outbounds(config)
    target_addresses = {canonical_endpoint_address(address) for address in (*deployment.endpoint_addresses, target_ip)}
    target_outbounds = [
        outbound for outbound in delivered if canonical_endpoint_address(outbound_address(outbound)) in target_addresses
    ]
    unsupported = [
        outbound_label(outbound) for outbound in target_outbounds if not is_supported_proxy_outbound(outbound)
    ]
    if unsupported:
        checks.append(
            build_verification_check(
                "unsupported_outbounds",
                "Delivered protocols",
                [
                    VerificationFinding(
                        code="UNSUPPORTED_OUTBOUND",
                        status="failed",
                        message="Unsupported canonical outbound(s): " + ", ".join(unsupported),
                        remediation="Update Meridian/Remnawave or remove unsupported delivered protocols.",
                    )
                ],
            )
        )
    selected_outbounds = [outbound for outbound in target_outbounds if is_supported_proxy_outbound(outbound)]
    if not target_outbounds:
        checks.append(
            build_verification_check(
                "target_outbounds",
                "Target protocols",
                [
                    VerificationFinding(
                        code="TARGET_OUTBOUNDS_MISSING",
                        status="failed",
                        message="Canonical subscription contains no outbound for the selected target",
                        remediation="Reconcile host bindings and retry after refreshing the subscription.",
                    )
                ],
            )
        )
        return checks, selected.username
    if not selected_outbounds:
        return checks, selected.username

    from meridian.xray_client import XRAY_BOOTSTRAP_TIMEOUT

    xray_binary = xray_provider(timeout=max(timeout, XRAY_BOOTSTRAP_TIMEOUT))
    if xray_binary is None:
        checks.append(
            build_verification_check(
                "xray_runtime",
                "Local Xray runtime",
                [
                    VerificationFinding(
                        code="XRAY_UNAVAILABLE",
                        status="skipped",
                        message="Pinned Xray client is unavailable; protocol traffic was not tested",
                        remediation="Check network access to the Xray release and retry.",
                    )
                ],
            )
        )
        return checks, selected.username

    checks.append(
        execute_xray_config(
            "automatic_fallback",
            "Automatic fallback",
            config,
            xray_binary,
            target_ip,
            connection_tester,
            expected_egress_ip="",
            timeout=timeout,
        )
    )
    for index, outbound in enumerate(selected_outbounds, start=1):
        tag = str(outbound["tag"])
        label = outbound_label(outbound)
        checks.append(
            execute_xray_config(
                f"protocol_{index}",
                label,
                config,
                xray_binary,
                target_ip,
                connection_tester,
                expected_egress_ip=deployment.expected_egress_ip,
                timeout=timeout,
                outbound_tag=tag,
            )
        )
    return checks, selected.username


def execute_xray_config(
    check_id: str,
    label: str,
    config: dict[str, Any],
    xray_binary: Path,
    target_ip: str,
    connection_tester: ConnectionTester,
    *,
    expected_egress_ip: str,
    timeout: float,
    outbound_tag: str = "",
) -> VerificationCheck:
    """Run one canonical route through a collision-free local SOCKS listener."""
    started = time.monotonic()
    try:
        selected = route_only(config, outbound_tag) if outbound_tag else config
        runnable, socks_port = use_free_local_ports(selected)
        success, detail = connection_tester(
            xray_binary,
            runnable,
            expected_egress_ip or target_ip,
            socks_port,
            label,
            bool(expected_egress_ip),
            timeout=timeout,
        )
    except ValueError as exc:
        findings = [
            VerificationFinding(
                code="PROTOCOL_CONFIG_INVALID",
                status="failed",
                message=f"{label} canonical config is invalid: {exc}",
                remediation="Repair the delivered subscription before retrying.",
            )
        ]
    except XrayStartupError as exc:
        findings = [
            VerificationFinding(
                code="PROTOCOL_TEST_UNAVAILABLE",
                status="skipped",
                message=f"{label} could not be executed: {exc}",
                remediation="Inspect the local Xray diagnostics and canonical subscription.",
            )
        ]
    else:
        if success is None:
            findings = [
                VerificationFinding(
                    code="PROTOCOL_TRAFFIC_INCONCLUSIVE",
                    status="skipped",
                    message=f"{label}: {detail}",
                    remediation="Retry when an external IP observer is reachable.",
                )
            ]
            return build_verification_check(
                check_id,
                label,
                findings,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        findings = [
            VerificationFinding(
                code="PROTOCOL_TRAFFIC_OK" if success else "PROTOCOL_TRAFFIC_FAILED",
                status="passed" if success else "failed",
                message=f"{label}: {detail}",
                remediation=(
                    "Inspect the delivered endpoint, credentials, firewall, and Xray logs." if not success else ""
                ),
            )
        ]
    return build_verification_check(
        check_id,
        label,
        findings,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


def select_active_client(panel: MeridianPanel, cluster: ClusterConfig, requested_client: str) -> User | None:
    """Choose a deterministic active user with a usable canonical subscription."""
    intent = cluster.topology_intent
    if requested_client:
        if intent is not None and requested_client not in intent.access.users:
            return None
        user = panel.get_user(requested_client)
        return user if _client_is_usable(user) else None
    if intent is not None:
        for username in intent.access.users:
            user = panel.get_user(username)
            if _client_is_usable(user):
                return user
        return None
    return next(
        (user for user in sorted(panel.list_users(), key=lambda item: item.username) if _client_is_usable(user)),
        None,
    )


def _client_is_usable(user: User | None) -> bool:
    return bool(user is not None and user.status.upper() == "ACTIVE" and user.short_uuid)
