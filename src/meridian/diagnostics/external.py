"""Client-side reachability and active-probe checks with no CLI rendering."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from meridian.core.verification import (
    VerificationCheck,
    VerificationFinding,
    VerificationStatus,
)
from meridian.diagnostics.evidence import finding, finish_check
from meridian.diagnostics.network import (
    certificate_identity,
    certificate_text,
    certificate_validation_error,
    get_certificate_der,
    https_get,
    negotiate_alpn,
    probe_tls_version,
    resolve_hostname,
    reverse_hostname,
)
from meridian.health import tcp_connect

_SUSPICIOUS_PORTS: dict[int, str] = {
    2053: "often used by VPN panels",
    2083: "often used by VPN panels",
    2087: "often used by VPN panels",
    2096: "often used by VPN panels",
    3000: "Remnawave panel must remain private",
    3010: "Remnawave node API must remain private",
    3020: "Remnawave subscription service must remain private",
    8080: "often used for proxy fallback",
    8443: "often used for proxy fallback",
    10000: "often used for management or proxy services",
}
_PROXY_PATHS = ("/ws", "/ray", "/v2ray", "/vmess", "/vless", "/trojan", "/grpc")
_CONTROL_PATH = "/qz8mf72k"
_PANEL_PATHS = ("/admin", "/panel", "/dashboard", "/login", "/api")


def check_port_surface(
    ip: str,
    *,
    expected_tcp_ports: tuple[int, ...] = (443,),
    timeout: float = 3,
) -> VerificationCheck:
    started = time.monotonic()
    findings: list[VerificationFinding] = []
    expected = tuple(sorted(set(expected_tcp_ports)))
    with ThreadPoolExecutor(max_workers=min(12, len(expected) + len(_SUSPICIOUS_PORTS) + 1)) as executor:
        expected_results = dict(
            zip(expected, executor.map(lambda port: tcp_connect(ip, port, timeout=timeout), expected), strict=True)
        )
        suspicious = {port: reason for port, reason in _SUSPICIOUS_PORTS.items() if port not in expected}
        suspicious_results = dict(
            zip(
                suspicious,
                executor.map(lambda port: tcp_connect(ip, port, timeout=timeout), suspicious),
                strict=True,
            )
        )
        http_open = tcp_connect(ip, 80, timeout=timeout)

    for port, reachable in expected_results.items():
        if reachable:
            findings.append(finding(f"PORT_{port}_OPEN", "passed", f"Expected TCP port {port} is reachable"))
        else:
            findings.append(
                finding(
                    f"PORT_{port}_CLOSED",
                    "failed",
                    f"Expected TCP port {port} is not reachable",
                    "Check the service, cloud firewall, and network path.",
                )
            )
    if http_open:
        findings.append(finding("PORT_80_OPEN", "passed", "TCP port 80 is open (normal for web servers)"))
    exposed = [port for port, reachable in suspicious_results.items() if reachable]
    for port in exposed:
        findings.append(
            finding(
                f"UNEXPECTED_PORT_{port}",
                "failed",
                f"Unexpected TCP port {port} is publicly reachable ({suspicious[port]})",
                f"Bind the service to localhost or block TCP/{port} at the host and cloud firewalls.",
            )
        )
    if not exposed:
        findings.append(finding("NO_UNEXPECTED_PORTS", "passed", "No known management or proxy ports are exposed"))
    return finish_check("port_surface", "Port surface", findings, started)


def check_http_response(
    ip: str,
    *,
    meridian_mode: bool,
    camouflage_origin: bool = False,
    server_name: str = "",
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    status, headers, body = https_get(
        ip,
        "/",
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
    )
    if status == 0:
        return finish_check(
            "http_response",
            "HTTP response",
            [finding("HTTPS_UNAVAILABLE", "skipped", "No HTTPS response was available for inspection")],
            started,
        )
    findings = [finding("HTTPS_RESPONSE", "passed", f"HTTPS root returned {status}")]
    if meridian_mode and status not in (403, 404):
        findings.append(
            finding(
                "UNEXPECTED_ROOT_STATUS",
                "failed",
                f"Meridian root returned {status}; expected 403 or 404",
                "Inspect the public nginx route and remove unintended root content.",
            )
        )
    server_header = headers.get("server", "")
    if "/" in server_header:
        findings.append(
            finding(
                "SERVER_VERSION_LEAK",
                "passed" if camouflage_origin else "failed" if meridian_mode else "warning",
                f"Server header exposes a version: {server_header}",
                "" if camouflage_origin else "Disable server version tokens.",
            )
        )
    elif server_header:
        findings.append(finding("SERVER_HEADER", "passed", f"Server header: {server_header}"))
    lowered = body.lower()
    if meridian_mode and any(marker in lowered for marker in (b"meridian", b"remnawave", b"xray", b"vless")):
        findings.append(
            finding(
                "ROOT_PRODUCT_LEAK",
                "failed",
                "Public root response exposes proxy product text",
                "Restore the stock nginx 403/404 response.",
            )
        )
    control_status, _, _ = https_get(
        ip,
        _CONTROL_PATH,
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
    )
    if control_status == 0:
        findings.append(finding("CONTROL_PATH_UNAVAILABLE", "skipped", "Random-path response could not be compared"))
    elif meridian_mode and control_status != 404:
        findings.append(
            finding(
                "CONTROL_PATH_DIFFERENTIAL",
                "failed",
                f"Random path returned {control_status}; expected 404",
                "Restore the default nginx 404 route.",
            )
        )
    else:
        findings.append(finding("CONTROL_PATH_BASELINE", "passed", f"Random path returned {control_status}"))
    return finish_check("http_response", "HTTP response", findings, started)


def check_tls_certificate(
    ip: str,
    *,
    server_name: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    identity = server_name or ip
    certificate = get_certificate_der(ip, identity, port=port, timeout=timeout)
    if not certificate:
        return finish_check(
            "tls_certificate",
            "TLS certificate",
            [
                finding(
                    "TLS_HANDSHAKE_FAILED",
                    "failed",
                    "TLS handshake did not return a peer certificate",
                    "Verify the TCP/443 listener and TLS/SNI routing.",
                )
            ],
            started,
        )
    findings = [finding("TLS_CERTIFICATE_PRESENT", "passed", "TLS handshake returned a certificate")]
    validation_error = certificate_validation_error(
        ip,
        identity,
        port=port,
        timeout=timeout,
    )
    if validation_error is None:
        findings.append(
            finding(
                "TLS_VALIDATION_UNAVAILABLE",
                "skipped",
                "Certificate trust and identity validation could not complete",
                "Retry from a network that can complete the validating TLS handshake.",
            )
        )
    elif validation_error:
        findings.append(
            finding(
                "TLS_CERTIFICATE_INVALID",
                "failed",
                f"Certificate trust, lifetime, or identity validation failed: {validation_error}",
                "Install a trusted, current certificate for the configured SNI.",
            )
        )
    else:
        findings.append(
            finding(
                "TLS_CERTIFICATE_VALID",
                "passed",
                f"Certificate is trusted and valid for {identity}",
            )
        )
    details = certificate_text(certificate)
    if details:
        dns_names = sorted(
            {
                part.strip().removeprefix("DNS:")
                for line in details.splitlines()
                if "DNS:" in line
                for part in line.split(",")
                if part.strip().startswith("DNS:")
            }
        )
        if dns_names:
            findings.append(
                finding(
                    "TLS_CERTIFICATE_NAMES",
                    "passed",
                    "Certificate names: " + ", ".join(dns_names[:3]),
                )
            )
    return finish_check("tls_certificate", "TLS certificate", findings, started)


def check_sni_consistency(
    ip: str,
    server_name: str,
    *,
    meridian_mode: bool,
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as executor:
        certificates = list(
            executor.map(
                lambda _index: get_certificate_der(ip, server_name, port=port, timeout=timeout),
                range(3),
            )
        )
    certificates = [certificate for certificate in certificates if certificate]
    if len(certificates) < 2:
        return finish_check(
            "sni_consistency",
            "SNI consistency",
            [finding("SNI_PROBES_INCOMPLETE", "skipped", f"Only {len(certificates)} of 3 SNI probes completed")],
            started,
        )
    identities = {certificate_identity(certificate) for certificate in certificates}
    if len(identities) == 1:
        findings = [finding("SNI_CONSISTENT", "passed", "Repeated SNI probes returned one certificate identity")]
    else:
        findings = [
            finding(
                "SNI_INCONSISTENT" if meridian_mode else "SNI_EDGE_VARIATION",
                "failed" if meridian_mode else "skipped",
                f"Repeated SNI probes returned {len(identities)} certificate identities",
                "Inspect deterministic SNI routing and upstream selection." if meridian_mode else "",
            )
        ]
    return finish_check("sni_consistency", "SNI consistency", findings, started)


def check_proxy_paths(
    ip: str,
    *,
    meridian_mode: bool,
    server_name: str = "",
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    baseline_status, _, baseline_body = https_get(
        ip,
        _CONTROL_PATH,
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
    )
    if baseline_status == 0:
        return finish_check(
            "proxy_paths",
            "Proxy paths",
            [finding("PATH_BASELINE_UNAVAILABLE", "skipped", "Random-path baseline was unavailable")],
            started,
        )
    with ThreadPoolExecutor(max_workers=len(_PROXY_PATHS)) as executor:
        responses = list(
            executor.map(
                lambda path: https_get(
                    ip,
                    path,
                    timeout=timeout,
                    server_name=server_name,
                    host_header=host_header or server_name,
                    port=port,
                ),
                _PROXY_PATHS,
            )
        )
    findings: list[VerificationFinding] = []
    for path, (status, _headers, body) in zip(_PROXY_PATHS, responses, strict=True):
        if status == 0:
            findings.append(finding("PATH_PROBE_UNAVAILABLE", "skipped", f"GET {path} did not complete"))
            continue
        if status == 101 or status != baseline_status or body != baseline_body:
            findings.append(
                finding(
                    "PROXY_PATH_DIFFERENTIAL",
                    "failed" if meridian_mode else "warning",
                    f"GET {path} differs from the random-path baseline (HTTP {status})",
                    "Use an unguessable transport path and return the default response elsewhere.",
                )
            )
    if not findings:
        findings.append(finding("PROXY_PATHS_UNIFORM", "passed", "Common proxy paths match the random-path response"))
    return finish_check("proxy_paths", "Proxy paths", findings, started)


def check_websocket_upgrade(
    ip: str,
    *,
    meridian_mode: bool,
    server_name: str = "",
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    status, _, _ = https_get(
        ip,
        "/",
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
        extra_headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version": "13",
        },
    )
    if status == 0:
        findings = [finding("WEBSOCKET_PROBE_UNAVAILABLE", "skipped", "WebSocket probe did not complete")]
    elif status == 101:
        findings = [
            finding(
                "ROOT_WEBSOCKET_UPGRADE",
                "failed" if meridian_mode else "warning",
                "Public root accepted a WebSocket upgrade",
                "Expose WebSocket only on its configured secret path.",
            )
        ]
    else:
        findings = [finding("ROOT_WEBSOCKET_REJECTED", "passed", f"Root rejected WebSocket upgrade (HTTP {status})")]
    return finish_check("websocket_upgrade", "WebSocket upgrade", findings, started)


def check_reverse_dns(ip: str, *, timeout: float = 3) -> VerificationCheck:
    started = time.monotonic()
    hostname = reverse_hostname(ip, timeout=timeout)
    if hostname is None:
        findings = [finding("REVERSE_DNS_UNAVAILABLE", "skipped", "Reverse DNS lookup timed out")]
    elif not hostname:
        findings = [finding("NO_REVERSE_DNS", "passed", "No reverse DNS record")]
    else:
        findings = [finding("REVERSE_DNS", "passed", f"PTR record: {hostname}")]
    return finish_check("reverse_dns", "Reverse DNS", findings, started)


def check_http2_support(
    ip: str,
    server_name: str,
    *,
    meridian_mode: bool,
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    protocol = negotiate_alpn(ip, server_name, ["h2", "http/1.1"], port=port, timeout=timeout)
    if protocol is None:
        findings = [finding("ALPN_UNAVAILABLE", "skipped", "ALPN negotiation did not complete")]
    else:
        status: VerificationStatus = "warning" if meridian_mode and protocol != "h2" else "passed"
        findings = [
            finding(
                "ALPN_PROTOCOL",
                status,
                f"Negotiated ALPN protocol: {protocol or 'none'}",
                "Enable HTTP/2 on the public TLS route." if status == "warning" else "",
            )
        ]
    return finish_check("http2_support", "HTTP/2 support", findings, started)


def check_legacy_tls(
    ip: str,
    server_name: str,
    *,
    port: int = 443,
    timeout: float = 3,
    route_kind: Literal["managed", "camouflage", "generic"] = "managed",
) -> VerificationCheck:
    started = time.monotonic()
    versions = (("TLS 1.0", "TLSv1"), ("TLS 1.1", "TLSv1_1"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(
            executor.map(
                lambda item: _tls_version_result(ip, port, server_name, item[1], timeout=timeout),
                versions,
            )
        )
    names = [item[0] for item, result in zip(versions, accepted, strict=True) if result == "accepted"]
    if names:
        status: VerificationStatus = (
            "failed" if route_kind == "managed" else "skipped" if route_kind == "camouflage" else "warning"
        )
        findings = [
            finding(
                "LEGACY_TLS_ACCEPTED" if route_kind == "managed" else "LEGACY_TLS_ORIGIN_POLICY",
                status,
                "Deprecated TLS accepted: " + ", ".join(names),
                "Restrict the TLS listener to TLS 1.2 and TLS 1.3." if route_kind != "camouflage" else "",
            )
        ]
    elif all(result == "rejected" for result in accepted):
        findings = [finding("LEGACY_TLS_REJECTED", "passed", "TLS 1.0 and TLS 1.1 are rejected")]
    else:
        findings = [
            finding(
                "LEGACY_TLS_INCONCLUSIVE",
                "skipped",
                "The local TLS stack could not distinguish protocol rejection from an unavailable handshake",
            )
        ]
    return finish_check("legacy_tls", "Legacy TLS", findings, started)


def _tls_version_result(
    ip: str,
    port: int,
    server_name: str,
    version: str,
    *,
    timeout: float,
) -> str:
    return probe_tls_version(ip, server_name, version, port=port, timeout=timeout)


def check_internal_ports(
    ip: str,
    ports: dict[str, int],
    *,
    timeout: float = 3,
) -> VerificationCheck:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, len(ports))) as executor:
        reachable = dict(
            zip(ports, executor.map(lambda item: tcp_connect(ip, item, timeout=timeout), ports.values()), strict=True)
        )
    exposed = [f"{name}={ports[name]}" for name, is_open in reachable.items() if is_open]
    if exposed:
        findings = [
            finding(
                "INTERNAL_PORT_EXPOSED",
                "failed",
                "Internal listeners are public: " + ", ".join(exposed),
                "Bind internal listeners to localhost or block them at both firewalls.",
            )
        ]
    else:
        findings = [finding("INTERNAL_PORTS_PRIVATE", "passed", "All known internal listeners are private")]
    return finish_check("internal_ports", "Internal ports", findings, started)


def check_root_indistinguishable(
    ip: str,
    *,
    server_name: str,
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    probes = (
        ("/favicon.ico", {404}, "Custom favicon is exposed"),
        ("/.env", {403, 404}, "Configuration path is not blocked"),
        ("/api", {403, 404}, "Root API path reaches an application"),
    )
    with ThreadPoolExecutor(max_workers=len(probes)) as executor:
        responses = list(
            executor.map(
                lambda item: https_get(
                    ip,
                    item[0],
                    timeout=timeout,
                    server_name=server_name,
                    host_header=host_header or server_name,
                    port=port,
                ),
                probes,
            )
        )
    findings: list[VerificationFinding] = []
    for (path, expected, detail), (status, _headers, _body) in zip(probes, responses, strict=True):
        if status == 0:
            findings.append(finding("ROOT_PATH_UNAVAILABLE", "skipped", f"GET {path} did not complete"))
        elif status not in expected:
            findings.append(
                finding(
                    "ROOT_PATH_DIFFERENTIAL",
                    "failed",
                    f"GET {path} returned {status}: {detail}",
                    "Restore the stock nginx deny/default routes.",
                )
            )
        else:
            findings.append(finding("ROOT_PATH_BLOCKED", "passed", f"GET {path} returned {status}"))
    return finish_check("root_indistinguishable", "Root indistinguishability", findings, started)


def check_domain_root(
    ip: str,
    domain: str,
    *,
    server_name: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    status, _, _ = https_get(
        ip,
        "/",
        timeout=timeout,
        server_name=server_name or domain,
        host_header=domain,
        port=port,
    )
    if status == 0:
        return finish_check(
            "domain_root",
            "Domain root",
            [finding("DOMAIN_ROOT_UNAVAILABLE", "skipped", f"HTTPS for {domain} did not complete")],
            started,
        )
    findings: list[VerificationFinding] = []
    if status in (403, 404):
        findings.append(finding("DOMAIN_ROOT_BLOCKED", "passed", f"Domain root returned {status}"))
    else:
        findings.append(
            finding(
                "DOMAIN_ROOT_CONTENT",
                "failed",
                f"Domain root returned {status}; expected 403 or 404",
                "Remove public root content from the Meridian hostname.",
            )
        )
    acme_status, _, acme_body = https_get(
        ip,
        "/.well-known/acme-challenge/",
        timeout=timeout,
        server_name=server_name or domain,
        host_header=domain,
        port=port,
    )
    if acme_status == 0:
        findings.append(finding("ACME_PATH_UNAVAILABLE", "skipped", "ACME path could not be inspected"))
    elif b"Index of" in acme_body or b"<pre>" in acme_body:
        findings.append(
            finding(
                "ACME_DIRECTORY_LISTING",
                "failed",
                "ACME challenge directory is listable",
                "Disable directory indexes for the ACME path.",
            )
        )
    else:
        findings.append(finding("ACME_PATH_PRIVATE", "passed", "ACME challenge directory is not listable"))
    return finish_check("domain_root", "Domain root", findings, started)


def check_secret_paths(
    ip: str,
    panel_path: str,
    *,
    server_name: str = "",
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(_PANEL_PATHS)) as executor:
        responses = list(
            executor.map(
                lambda path: https_get(
                    ip,
                    path,
                    timeout=timeout,
                    server_name=server_name,
                    host_header=host_header or server_name,
                    port=port,
                ),
                _PANEL_PATHS,
            )
        )
    findings: list[VerificationFinding] = []
    for path, (status, _headers, _body) in zip(_PANEL_PATHS, responses, strict=True):
        if status == 0:
            findings.append(finding("COMMON_PANEL_PATH_UNAVAILABLE", "skipped", f"GET {path} did not complete"))
        elif status not in (403, 404):
            findings.append(
                finding(
                    "COMMON_PANEL_PATH_EXPOSED",
                    "failed",
                    f"Common panel path {path} returned {status}",
                    "Proxy panel traffic only through the generated secret path.",
                )
            )
    secret_status, _, _ = https_get(
        ip,
        f"/{panel_path.strip('/')}/",
        timeout=timeout,
        server_name=server_name,
        host_header=host_header or server_name,
        port=port,
    )
    if secret_status == 0:
        findings.append(finding("SECRET_PANEL_UNAVAILABLE", "skipped", "Configured panel path did not respond"))
    elif secret_status in (200, 301, 302):
        findings.append(finding("SECRET_PANEL_AVAILABLE", "passed", "Configured secret panel path is reachable"))
    else:
        findings.append(
            finding(
                "SECRET_PANEL_BROKEN",
                "failed",
                f"Configured panel path returned {secret_status}",
                "Repair the nginx panel route or Remnawave backend.",
            )
        )
    if not findings:
        findings.append(finding("PANEL_PATHS_ISOLATED", "passed", "Common panel paths are blocked"))
    return finish_check("secret_paths", "Secret path isolation", findings, started)


def check_sni_camouflage(
    ip: str,
    expected_sni: str,
    *,
    port: int = 443,
    timeout: float = 5,
) -> VerificationCheck:
    started = time.monotonic()
    server_certificate = get_certificate_der(ip, expected_sni, port=port, timeout=timeout)
    if not server_certificate:
        return finish_check(
            "sni_camouflage",
            "SNI camouflage",
            [
                finding(
                    "CAMOUFLAGE_SERVER_UNAVAILABLE",
                    "skipped",
                    "Server did not complete the camouflage SNI handshake",
                )
            ],
            started,
        )
    direct_ip = resolve_hostname(expected_sni, timeout=timeout)
    if not direct_ip:
        return finish_check(
            "sni_camouflage",
            "SNI camouflage",
            [finding("CAMOUFLAGE_ORIGIN_UNAVAILABLE", "skipped", "Camouflage origin could not be reached directly")],
            started,
        )
    direct_certificate = get_certificate_der(direct_ip, expected_sni, timeout=timeout)
    if not direct_certificate:
        return finish_check(
            "sni_camouflage",
            "SNI camouflage",
            [finding("CAMOUFLAGE_ORIGIN_UNAVAILABLE", "skipped", "Camouflage origin could not be reached directly")],
            started,
        )
    server_validation = certificate_validation_error(
        ip,
        expected_sni,
        port=port,
        timeout=timeout,
    )
    direct_validation = certificate_validation_error(
        direct_ip,
        expected_sni,
        timeout=timeout,
    )
    if server_validation is None or direct_validation is None:
        return finish_check(
            "sni_camouflage",
            "SNI camouflage",
            [
                finding(
                    "CAMOUFLAGE_VALIDATION_UNAVAILABLE",
                    "skipped",
                    f"Certificate trust and hostname validation for {expected_sni} could not complete",
                )
            ],
            started,
        )
    if server_validation or direct_validation:
        detail = server_validation or direct_validation
        return finish_check(
            "sni_camouflage",
            "SNI camouflage",
            [
                finding(
                    "CAMOUFLAGE_CERTIFICATE_INVALID",
                    "failed",
                    f"Certificate trust or hostname validation failed for {expected_sni}: {detail}",
                    "Verify the Reality destination and camouflage SNI.",
                )
            ],
            started,
        )
    if certificate_identity(server_certificate) == certificate_identity(direct_certificate):
        findings = [
            finding(
                "CAMOUFLAGE_MATCH",
                "passed",
                f"SNI {expected_sni} matches the direct certificate identity",
            )
        ]
    else:
        findings = [
            finding(
                "CAMOUFLAGE_EDGE_VARIATION",
                "skipped",
                (
                    f"Both endpoints returned trusted certificates for {expected_sni}, "
                    "but independently resolved edges used different certificate identities"
                ),
            )
        ]
    return finish_check("sni_camouflage", "SNI camouflage", findings, started)
