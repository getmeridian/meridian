"""Censor probe — test what a censor sees when they investigate your server."""

from __future__ import annotations

import hashlib
import http.client
import shlex
import socket
import ssl
import subprocess
from dataclasses import dataclass, field

from meridian.commands.resolve import resolve_server
from meridian.config import SERVERS_FILE, is_ip
from meridian.console import err_console, info, line, ok, warn
from meridian.resolve import is_local_keyword
from meridian.servers import ServerRegistry
from meridian.ssh import tcp_connect

# Ports that suggest VPN/proxy infrastructure
_SUSPICIOUS_PORTS: dict[int, str] = {
    8080: "often used for proxy fallback",
    8443: "often used for proxy fallback",
    2053: "often used by VPN panels (3x-ui / x-ui)",
    2083: "often used by VPN panels",
    2087: "often used by VPN panels",
    2096: "often used by VPN panels",
    10000: "often used by Webmin / proxy management",
    3000: "Remnawave panel (should be localhost-only)",
    3010: "Remnawave node API (should be localhost-only)",
    3020: "Remnawave subscription page (should be localhost-only)",
}

# Paths commonly used by V2Ray/Xray/Trojan proxy transports
_PROXY_PATHS = ["/ws", "/ray", "/v2ray", "/vmess", "/vless", "/trojan", "/grpc"]

# Control path unlikely to match any real route
_CONTROL_PATH = "/qz8mf72k"

# Stock nginx error page body length (server_tokens off)
_NGINX_STOCK_LENGTH = 146


# Finding = (is_ok, message) — True means pass, False means warning
Finding = tuple[bool, str]


@dataclass
class CheckResult:
    """Result from a single probe check."""

    name: str
    passed: bool
    findings: list[Finding] = field(default_factory=list)


def _ssl_context() -> ssl.SSLContext:
    """Create an SSL context that accepts any certificate (censor doesn't validate)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _https_get(
    ip: str,
    path: str,
    timeout: int = 5,
    extra_headers: dict[str, str] | None = None,
    port: int = 443,
) -> tuple[int, dict[str, str], bytes]:
    """HTTPS GET to an IP. Returns (status, headers_dict, body).

    Returns (0, {}, b"") on connection failure.
    """
    try:
        conn = http.client.HTTPSConnection(ip, port=port, timeout=timeout, context=_ssl_context())
        headers = {"Host": ip, "User-Agent": "Mozilla/5.0"}
        if extra_headers:
            headers.update(extra_headers)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read(4096)
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        status = resp.status
        conn.close()
        return status, resp_headers, body
    except (OSError, http.client.HTTPException):  # Socket, SSL, and HTTP protocol errors
        return 0, {}, b""


def _get_cert_der(ip: str, sni: str, timeout: int = 5) -> bytes:
    """Connect to ip:443 with given SNI and return the DER-encoded certificate.

    Returns empty bytes on failure.
    """
    try:
        ctx = _ssl_context()
        with socket.create_connection((ip, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=sni) as ssock:
                der = ssock.getpeercert(binary_form=True)
                return der or b""
    except (OSError, ssl.SSLError):  # Socket and TLS handshake errors
        return b""


# ---------------------------------------------------------------------------
# Check 1: Port surface
# ---------------------------------------------------------------------------


def check_ports(ip: str) -> CheckResult:
    """Scan for ports that reveal proxy infrastructure."""
    result = CheckResult(name="Port surface", passed=True)

    # Port 443
    if tcp_connect(ip, 443, timeout=3):
        result.findings.append((True, "Port 443 is open"))
    else:
        result.passed = False
        result.findings.append((False, "Port 443 is not reachable"))

    # Port 80 — acceptable either way
    if tcp_connect(ip, 80, timeout=3):
        result.findings.append((True, "Port 80 is open (normal for web servers)"))

    # Suspicious ports
    for port, description in _SUSPICIOUS_PORTS.items():
        if tcp_connect(ip, port, timeout=3):
            # Verify with HTTPS — middleboxes complete TCP but don't serve real content
            status, _, _ = _https_get(ip, "/", timeout=3, port=port)
            if status > 0:
                result.passed = False
                result.findings.append((False, f"Port {port} is open ({description})"))
            else:
                result.findings.append(
                    (True, f"Port {port} TCP-reachable but no service detected (network infrastructure)")
                )

    if result.passed:
        result.findings.append((True, "No unexpected ports open"))

    return result


# ---------------------------------------------------------------------------
# Check 2: HTTP response
# ---------------------------------------------------------------------------


def check_http_response(ip: str) -> CheckResult:
    """Check if HTTP responses look like stock nginx."""
    result = CheckResult(name="HTTP response", passed=True)

    # Root request
    status, headers, body = _https_get(ip, "/")
    if status == 0:
        result.findings.append((True, "Could not connect to HTTPS (skipped)"))
        return result

    # Check status code
    if status in (403, 404):
        result.findings.append((True, f"Root returns {status}"))
    else:
        result.passed = False
        result.findings.append((False, f"Root returns {status} — expected 403 or 404"))

    # Check body length
    content_length = len(body)
    if content_length == _NGINX_STOCK_LENGTH:
        result.findings.append((True, f"Stock nginx error page ({_NGINX_STOCK_LENGTH} bytes)"))
    elif content_length > 0:
        body_hash = hashlib.sha256(body).hexdigest()[:12]
        result.passed = False
        result.findings.append(
            (
                False,
                f"Custom error page ({content_length} bytes, hash:{body_hash}) — same hash scannable across IPs",
            )
        )

    # Check Server header
    server_header = headers.get("server", "")
    if server_header == "nginx":
        result.findings.append((True, "Server: nginx (no version leak)"))
    elif "/" in server_header:
        result.passed = False
        result.findings.append((False, f"Server header leaks version: {server_header}"))
    elif server_header:
        result.findings.append((True, f"Server: {server_header}"))

    # Random path — should also return stock response
    r_status, _, r_body = _https_get(ip, _CONTROL_PATH)
    if r_status == 404 and len(r_body) == _NGINX_STOCK_LENGTH:
        result.findings.append((True, "Random path returns stock 404"))
    elif r_status != 0:
        result.passed = False
        if r_status == 404:
            result.findings.append(
                (False, f"Random path body is non-standard ({len(r_body)} bytes, expected {_NGINX_STOCK_LENGTH})")
            )
        else:
            result.findings.append((False, f"Random path returns {r_status} — expected 404"))

    return result


# ---------------------------------------------------------------------------
# Check 3: TLS certificate
# ---------------------------------------------------------------------------


def check_tls_certificate(ip: str) -> CheckResult:
    """Inspect the TLS certificate for information leaks."""
    result = CheckResult(name="TLS certificate", passed=True)

    # Try openssl for detailed cert inspection
    cert_text = _get_cert_text_via_openssl(ip)
    if not cert_text:
        # Fallback: just check if TLS handshake works
        der = _get_cert_der(ip, ip)
        if der:
            result.findings.append((True, "TLS handshake OK (install openssl for detailed cert analysis)"))
        else:
            result.findings.append((True, "TLS handshake failed (skipped)"))
        return result

    # Parse cert details
    # Check for domain names in Subject/SAN
    domain_names: list[str] = []
    for cert_line in cert_text.splitlines():
        stripped = cert_line.strip()
        if stripped.startswith("DNS:"):
            for part in stripped.split(","):
                part = part.strip()
                if part.startswith("DNS:"):
                    domain_names.append(part[4:])
        elif "DNS:" in stripped and "Subject Alternative Name" not in stripped:
            for part in stripped.split(","):
                part = part.strip()
                if part.startswith("DNS:"):
                    domain_names.append(part[4:])

    if domain_names:
        result.passed = False
        names = ", ".join(domain_names[:3])
        result.findings.append(
            (False, f"Certificate reveals domain(s): {names} — associates this IP with a known domain")
        )
    else:
        result.findings.append((True, "Certificate has no domain names"))

    # Check issuer
    for cert_line in cert_text.splitlines():
        if "Issuer:" in cert_line:
            issuer = cert_line.split("Issuer:", 1)[1].strip()
            if "Let's Encrypt" in issuer:
                result.findings.append((True, "Issuer: Let's Encrypt"))
            elif issuer:
                result.findings.append((True, f"Issuer: {issuer}"))
            break

    return result


def _get_cert_text_via_openssl(ip: str) -> str:
    """Get certificate text using openssl subprocess. Returns empty string on failure."""
    q_ip = shlex.quote(ip)
    # openssl -connect requires brackets for IPv6: [2001:db8::1]:443
    connect_host = f"[{ip}]" if ":" in ip else ip
    q_connect = shlex.quote(f"{connect_host}:443")
    try:
        proc = subprocess.run(
            [
                "bash",
                "-c",
                f"echo | openssl s_client -connect {q_connect} -servername {q_ip} 2>/dev/null"
                " | openssl x509 -text -noout 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


# ---------------------------------------------------------------------------
# Check 4: SNI consistency
# ---------------------------------------------------------------------------


def _cert_identity(der: bytes) -> str:
    """Extract subject+issuer identity from DER cert bytes.

    Uses openssl for semantic comparison (handles CDN cert rotation).
    Falls back to sha256 hex if openssl is unavailable.
    """
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-noout", "-subject", "-issuer"],
            input=der,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().decode(errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return hashlib.sha256(der).hexdigest()


def check_sni_consistency(ip: str) -> CheckResult:
    """Test if repeated connections with the same unknown SNI are consistent.

    A Meridian server TCP-proxies unknown SNIs to the Reality dest site.
    We test consistency by connecting multiple times with the SAME SNI
    and verifying we get the same certificate each time. Different SNIs
    may legitimately produce different certs (the dest CDN routes by SNI),
    so we don't compare across different SNI values.
    """
    result = CheckResult(name="SNI consistency", passed=True)

    # Use a single plausible SNI for consistency testing
    test_sni = "example.com"
    certs: list[bytes] = []

    for _ in range(3):
        der = _get_cert_der(ip, test_sni)
        if der:
            certs.append(der)

    if len(certs) < 2:
        result.findings.append((True, "Could not complete SNI probes (skipped)"))
        return result

    # Compare all certs by identity (subject+issuer), not raw DER bytes.
    # CDNs return different cert instances from different edge nodes;
    # same subject+issuer means the routing destination is consistent.
    identities = [_cert_identity(c) for c in certs]
    if len(set(identities)) == 1:
        result.findings.append((True, "SNI routing is consistent (same cert on repeated probes)"))
    else:
        result.passed = False
        unique = len(set(identities))
        result.findings.append(
            (
                False,
                f"{unique} different certificates across {len(certs)} probes with same SNI — "
                "routing is inconsistent (expected for relay nodes, investigate for exit servers)",
            )
        )

    return result


# ---------------------------------------------------------------------------
# Check 5: Proxy path probing
# ---------------------------------------------------------------------------


def check_proxy_paths(ip: str) -> CheckResult:
    """Probe common proxy transport paths for differential behavior."""
    result = CheckResult(name="Proxy paths", passed=True)

    # Get baseline from control path
    ctrl_status, _, ctrl_body = _https_get(ip, _CONTROL_PATH)
    if ctrl_status == 0:
        result.findings.append((True, "Could not connect (skipped)"))
        return result

    anomalies: list[str] = []
    for path in _PROXY_PATHS:
        status, _, body = _https_get(ip, path)
        if status == 101:
            anomalies.append(f"{path} → 101 Switching Protocols (proxy endpoint)")
        elif status != ctrl_status:
            anomalies.append(f"{path} → {status} (other paths return {ctrl_status})")
        elif len(body) != len(ctrl_body):
            anomalies.append(f"{path} → responds differently than a random path")

    if anomalies:
        result.passed = False
        for a in anomalies:
            result.findings.append((False, a))
    else:
        result.findings.append((True, "All proxy paths behave identically"))

    return result


# ---------------------------------------------------------------------------
# Check 6: WebSocket upgrade
# ---------------------------------------------------------------------------


def check_websocket_upgrade(ip: str) -> CheckResult:
    """Test if the server accepts WebSocket upgrades (proxy indicator)."""
    result = CheckResult(name="WebSocket upgrade", passed=True)

    status, _, _ = _https_get(
        ip,
        "/",
        extra_headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version": "13",
        },
    )

    if status == 0:
        result.findings.append((True, "Could not connect (skipped)"))
        return result

    if status == 101:
        result.passed = False
        result.findings.append((False, "Server accepts WebSocket upgrade — indicates proxy transport"))
    else:
        result.findings.append((True, "WebSocket upgrade rejected"))

    return result


# ---------------------------------------------------------------------------
# Check 7: Reverse DNS
# ---------------------------------------------------------------------------


def check_reverse_dns(ip: str) -> CheckResult:
    """Check if reverse DNS reveals hosting provider or suspicious PTR records."""
    result = CheckResult(name="Reverse DNS", passed=True)

    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror, OSError):
        result.findings.append((True, "No reverse DNS record"))
        return result

    result.findings.append((True, f"PTR: {hostname}"))
    return result


# ---------------------------------------------------------------------------
# Check 8: HTTP/2 support (ALPN)
# ---------------------------------------------------------------------------


def check_http2_support(ip: str) -> CheckResult:
    """Check if the server negotiates HTTP/2 via ALPN — missing h2 is unusual for modern nginx."""
    result = CheckResult(name="HTTP/2 support", passed=True)

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        sock = socket.create_connection((ip, 443), timeout=5)
        ssock = ctx.wrap_socket(sock, server_hostname=ip)
        alpn = ssock.selected_alpn_protocol()
        ssock.close()
    except (OSError, ssl.SSLError):  # Socket and TLS handshake errors
        result.findings.append((True, "Could not check ALPN (connection failed)"))
        return result

    if alpn == "h2":
        result.findings.append((True, "HTTP/2 negotiated"))
    elif alpn == "http/1.1":
        result.passed = False
        result.findings.append((False, "Only HTTP/1.1 — missing h2 is unusual for modern servers"))
    elif alpn:
        result.findings.append((True, f"ALPN: {alpn}"))
    else:
        result.findings.append((True, "No ALPN negotiated"))

    return result


# ---------------------------------------------------------------------------
# Check 9: Legacy TLS versions
# ---------------------------------------------------------------------------


def check_legacy_tls(ip: str) -> CheckResult:
    """Check if the server accepts legacy TLS 1.0/1.1 — modern servers should reject them."""
    result = CheckResult(name="Legacy TLS", passed=True)

    accepted: list[str] = []
    for proto_name, proto_const in [
        ("TLS 1.0", ssl.TLSVersion.TLSv1),
        ("TLS 1.1", ssl.TLSVersion.TLSv1_1),
    ]:
        if _tls_version_accepted(ip, proto_const):
            accepted.append(proto_name)

    if accepted:
        result.passed = False
        versions = " + ".join(accepted)
        result.findings.append((False, f"Accepts {versions} — modern servers reject deprecated TLS versions"))
    else:
        result.findings.append((True, "Only TLS 1.2+ accepted"))

    return result


def _tls_version_accepted(ip: str, version: ssl.TLSVersion) -> bool:
    """Test if the server accepts a specific TLS version."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = version
        ctx.maximum_version = version
        sock = socket.create_connection((ip, 443), timeout=3)
        ssock = ctx.wrap_socket(sock, server_hostname=ip)
        ssock.close()
        return True
    except (OSError, ssl.SSLError):  # Socket and TLS errors (expected when version rejected)
        return False


def _resolve_domain(domain: str) -> str:
    """Resolve a domain name to an IP address. Prefers IPv4, falls back to IPv6."""
    # Try IPv4 first
    try:
        results = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
        if results:
            return str(results[0][4][0])
    except (socket.gaierror, OSError):
        pass
    # Fall back to IPv6
    try:
        results = socket.getaddrinfo(domain, 443, socket.AF_INET6, socket.SOCK_STREAM)
        if results:
            return str(results[0][4][0])
    except (socket.gaierror, OSError):
        pass
    return ""


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def run(
    ip: str = "",
    requested_server: str = "",
) -> None:
    """Probe a server as a censor would — check if the deployment is detectable."""
    target = ip or requested_server
    domain = ""

    # Resolve domain to IP if needed
    if target and not is_ip(target) and not is_local_keyword(target):
        resolved_ip = _resolve_domain(target)
        if resolved_ip:
            domain = target
            ip = resolved_ip
        # else: let resolve_server handle it (might be a registry name)

    registry = ServerRegistry(SERVERS_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip)

    # Header
    if domain:
        label = f"{domain} ({resolved.ip})"
    else:
        label = resolved.ip

    err_console.print()
    err_console.print("  [bold]Censor Probe[/bold]")
    err_console.print(f"  [dim]Testing what a censor sees when they investigate {label}[/dim]")
    err_console.print()

    issues = 0
    checks_run = 0

    # -- Check 1: Port surface --
    info("Scanning port surface...")
    port_result = check_ports(resolved.ip)
    checks_run += 1

    _print_result(port_result)
    if not port_result.passed:
        issues += 1

    # -- Check 2: HTTP response --
    info("Checking HTTP response (IP-based access)...")
    http_result = check_http_response(resolved.ip)
    checks_run += 1
    _print_result(http_result)
    if not http_result.passed:
        issues += 1

    # -- Check 3: TLS certificate --
    info("Inspecting TLS certificate...")
    cert_result = check_tls_certificate(resolved.ip)
    checks_run += 1
    _print_result(cert_result)
    if not cert_result.passed:
        issues += 1

    # -- Check 4: SNI consistency --
    info("Testing SNI consistency...")
    sni_result = check_sni_consistency(resolved.ip)
    checks_run += 1
    _print_result(sni_result)
    if not sni_result.passed:
        issues += 1

    # -- Check 5: Proxy paths --
    info("Probing common proxy paths...")
    path_result = check_proxy_paths(resolved.ip)
    checks_run += 1
    _print_result(path_result)
    if not path_result.passed:
        issues += 1

    # -- Check 6: WebSocket upgrade --
    info("Testing WebSocket upgrade...")
    ws_result = check_websocket_upgrade(resolved.ip)
    checks_run += 1
    _print_result(ws_result)
    if not ws_result.passed:
        issues += 1

    # -- Check 7: Reverse DNS --
    info("Checking reverse DNS...")
    rdns_result = check_reverse_dns(resolved.ip)
    checks_run += 1
    _print_result(rdns_result)
    if not rdns_result.passed:
        issues += 1

    # -- Check 8: HTTP/2 support --
    info("Checking HTTP/2 support...")
    h2_result = check_http2_support(resolved.ip)
    checks_run += 1
    _print_result(h2_result)
    if not h2_result.passed:
        issues += 1

    # -- Check 9: Legacy TLS --
    info("Checking legacy TLS versions...")
    tls_result = check_legacy_tls(resolved.ip)
    checks_run += 1
    _print_result(tls_result)
    if not tls_result.passed:
        issues += 1

    # -- Verdict --
    err_console.print()
    line()
    err_console.print()

    if issues == 0:
        err_console.print(f"  [ok][bold]All {checks_run} checks passed.[/bold][/ok] No obvious proxy indicators found.")
    else:
        err_console.print(
            f"  [warn][bold]{issues} issue(s) found.[/bold][/warn]"
            " These patterns can help a censor distinguish this server from a regular web server."
        )
    err_console.print()


def _print_result(result: CheckResult) -> None:
    """Print check result findings using console helpers."""
    for is_ok, message in result.findings:
        if is_ok:
            ok(message)
        else:
            warn(message)
