"""Client-vantage checks that answer "what is blocked?" from TLS flow evidence."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial

from meridian.core.verification import (
    VerificationCheck,
    VerificationFinding,
    VerificationStatus,
    build_verification_check,
)
from meridian.diagnostics.network import resolve_hostname
from meridian.diagnostics.tls_probe import TlsFlowResult, http_get_request, probe_tls_flow
from meridian.verification import DeploymentVerificationContext

VANTAGE_NOTE = "Measured from this machine; it only reflects censorship when run from inside the censored network."

# TSPU-style throttling freezes flagged TLS flows after roughly 14-24 KB; anything
# past this many bytes has cleared the usual freeze point.
STALL_CLEARANCE_BYTES = 32 * 1024
THROTTLE_WINDOW = (8 * 1024, STALL_CLEARANCE_BYTES)

FlowProber = Callable[..., TlsFlowResult]
Resolver = Callable[..., str | None]


def check_block_differential(
    ip: str,
    camouflage_sni: str,
    *,
    port: int = 443,
    timeout: float = 5,
    check_id: str = "block_differential",
    prober: FlowProber = probe_tls_flow,
    resolver: Resolver = resolve_hostname,
) -> VerificationCheck:
    """Compare the server with its camouflage SNI, with no SNI, and the real camouflage site."""
    started = time.monotonic()
    name = "Server IP vs camouflage SNI"
    camouflage = prober(ip, camouflage_sni, port=port, timeout=timeout)
    if camouflage.stage == "unavailable":
        return _check(
            check_id, name, started, "BLOCK_PROBE_UNAVAILABLE", "skipped", "The local TLS probe could not run"
        )
    if camouflage.server_answered_tls:
        return _check(
            check_id,
            name,
            started,
            "SERVER_REACHABLE",
            "passed",
            f"The server answers TLS for {camouflage_sni} ({camouflage.describe()}). {VANTAGE_NOTE}",
        )

    if not camouflage.silenced:
        return _check(
            check_id,
            name,
            started,
            "BLOCK_DIFFERENTIAL_INCONCLUSIVE",
            "skipped",
            f"The server refused the connection ({camouflage.describe()}) in a way that does not look like "
            f"filtering. {VANTAGE_NOTE}",
            "Run `meridian check` to confirm the server's listeners.",
        )

    neutral = prober(ip, "", port=port, timeout=timeout)
    if neutral.server_answered_tls:
        if camouflage.stage != "handshake":
            return _check(
                check_id,
                name,
                started,
                "BLOCK_DIFFERENTIAL_INCONCLUSIVE",
                "skipped",
                f"The first connection failed ({camouflage.describe()}) but the next one got through, so the "
                f"result is unstable. {VANTAGE_NOTE}",
                "Retry in a minute.",
            )
        control = _real_site_flow(camouflage_sni, port=port, timeout=timeout, prober=prober, resolver=resolver)
        where = (
            "while the real site opens directly, so the SNI is being filtered on this server's address"
            if control is not None and control.server_answered_tls
            else "and the real site does not open either, so the SNI itself looks filtered"
        )
        return _check(
            check_id,
            name,
            started,
            "CAMOUFLAGE_SNI_BLOCKED",
            "failed",
            f"Camouflage SNI looks blocked: {camouflage_sni} is cut off ({camouflage.describe()}) but a "
            f"connection without it gets through, {where}. {VANTAGE_NOTE}",
            "Choose another camouflage SNI (one that plausibly lives in your hosting provider's network), "
            "then re-run setup.",
        )

    control = _real_site_flow(camouflage_sni, port=port, timeout=timeout, prober=prober, resolver=resolver)
    if control is None or not control.server_answered_tls:
        return _check(
            check_id,
            name,
            started,
            "BLOCK_DIFFERENTIAL_INCONCLUSIVE",
            "skipped",
            f"The server did not answer ({camouflage.describe()}), and the real {camouflage_sni} could not be "
            f"reached directly either, so this network may be offline or filtering everything. {VANTAGE_NOTE}",
            "Retry from a network that can open ordinary websites.",
        )
    return _check(
        check_id,
        name,
        started,
        "SERVER_IP_BLOCKED",
        "failed",
        f"Server IP looks blocked: every connection to it fails ({camouflage.describe()}) while the real "
        f"{camouflage_sni} opens directly. {VANTAGE_NOTE}",
        "Confirm the server is up with `meridian check`; if it is, ask your provider for a new IP or move "
        "to another provider, then redeploy on the new address.",
    )


def check_data_stall(
    ip: str,
    path: str,
    *,
    server_name: str = "",
    host_header: str = "",
    port: int = 443,
    timeout: float = 5,
    check_id: str = "data_stall",
    name: str = "Data flow past 16-24 KB",
    via_cdn: bool = False,
    prober: FlowProber = probe_tls_flow,
) -> VerificationCheck:
    """Fetch a Meridian-served asset larger than the throttling freeze point and watch for a freeze."""
    started = time.monotonic()
    host = host_header or server_name or (f"[{ip}]" if ":" in ip else ip)
    flow = prober(
        ip,
        server_name,
        port=port,
        request=http_get_request(path, host),
        timeout=timeout,
        stall_timeout=timeout,
        max_bytes=256 * 1024,
    )
    if flow.stage in {"unavailable", "connect", "handshake"}:
        return _check(
            check_id,
            name,
            started,
            "DATA_FLOW_UNAVAILABLE",
            "skipped",
            f"Could not start a transfer ({flow.describe()}); reachability is reported by the other checks.",
            "Fix reachability first, then retry.",
        )
    if flow.stalled or (flow.stage == "transfer" and flow.error_kind in {"reset", "closed"}):
        low, high = THROTTLE_WINDOW
        throttled = low <= flow.bytes_received <= high
        if via_cdn:
            remediation = (
                "Cloudflare-proxied traffic from Russia often dies after about 20 KB. Prefer the direct "
                "Reality or XHTTP paths for these users, or move the CDN path to another provider."
            )
        else:
            remediation = (
                "This is how TSPU-style throttling treats a flagged foreign IP. Ask your provider for a new IP "
                "or move to another provider, then redeploy on the new address."
            )
        message = (
            f"Transfer froze after {flow.bytes_received / 1024:.1f} KB, the signature of throttling"
            if throttled
            else f"Transfer stopped after {flow.bytes_received / 1024:.1f} KB ({flow.describe()})"
        )
        return _check(
            check_id, name, started, "DATA_FLOW_STALLED", "warning", f"{message}. {VANTAGE_NOTE}", remediation
        )
    if flow.bytes_received < STALL_CLEARANCE_BYTES:
        answered = f"HTTP {flow.http_status}" if flow.http_status else "a short response"
        return _check(
            check_id,
            name,
            started,
            "DATA_FLOW_TOO_SMALL",
            "skipped",
            f"The server answered with {answered} ({flow.describe()}), too little to judge throttling.",
            "Redeploy so the connection-page assets are present, then retry.",
        )
    first_byte = f", first byte after {flow.first_byte_ms} ms" if flow.first_byte_ms is not None else ""
    return _check(
        check_id,
        name,
        started,
        "DATA_FLOW_OK",
        "passed",
        f"Received {flow.bytes_received / 1024:.1f} KB without a freeze{first_byte}. {VANTAGE_NOTE}",
    )


def _real_site_flow(
    host: str,
    *,
    port: int,
    timeout: float,
    prober: FlowProber,
    resolver: Resolver,
) -> TlsFlowResult | None:
    address = resolver(host, timeout=timeout)
    if not address:
        return None
    return prober(address, host, port=port, timeout=timeout)


def _check(
    check_id: str,
    name: str,
    started: float,
    code: str,
    status: VerificationStatus,
    message: str,
    remediation: str = "",
) -> VerificationCheck:
    finding = VerificationFinding(
        code=code,
        status=status,
        message=message,
        remediation=remediation if status != "passed" else "",
    )
    return build_verification_check(
        check_id, name, [finding], duration_ms=max(0, int((time.monotonic() - started) * 1000))
    )


def blocking_jobs(
    ip: str,
    context: DeploymentVerificationContext,
    *,
    requested_sni: str = "",
    timeout: float = 5,
    differential: bool = True,
) -> list[Callable[[], VerificationCheck]]:
    """Select differential and data-flow checks that the deployment's facts make meaningful.

    The data-flow probe fetches the connection-page bundle (``pwa/app.js``, ~50 KB),
    which only the control server serves; the CDN variant runs when a WSS path
    exists and the control hostname resolves somewhere other than the server.
    """
    jobs: list[Callable[[], VerificationCheck]] = []
    if differential:
        default_port = context.public_tls_ports[0] if context.public_tls_ports else 443
        snis = {sni: context.reality_sni_ports.get(sni, default_port) for sni in context.reality_snis}
        if requested_sni and requested_sni not in snis:
            snis[requested_sni] = default_port
        for sni, port in sorted(snis.items()):
            check_id = "block_differential" if len(snis) == 1 else f"block_differential:{sni}"
            jobs.append(partial(check_block_differential, ip, sni, port=port, timeout=timeout, check_id=check_id))

    panel_route = next((route for route in context.https_routes if route.panel), None)
    if context.connection_page_path and panel_route is not None:
        path = f"/{context.connection_page_path}/pwa/app.js"
        jobs.append(
            partial(
                check_data_stall,
                ip,
                path,
                server_name=panel_route.tls_sni,
                host_header=panel_route.host_header,
                port=panel_route.port,
                timeout=timeout,
            )
        )
        if "wss" in context.protocols and panel_route.host_header:
            jobs.append(partial(check_cdn_data_stall, ip, panel_route.host_header, path, timeout=timeout))
    return jobs


def check_cdn_data_stall(
    ip: str,
    hostname: str,
    path: str,
    *,
    timeout: float = 5,
    resolver: Resolver = resolve_hostname,
    prober: FlowProber = probe_tls_flow,
) -> VerificationCheck:
    """Run the data-flow probe through the hostname's public address when it is a CDN edge."""
    started = time.monotonic()
    check_id, name = "cdn_data_stall", "CDN data flow past 16-24 KB"
    edge = resolver(hostname, timeout=timeout)
    if edge is None:
        return _check(
            check_id, name, started, "CDN_DNS_UNAVAILABLE", "skipped", f"Could not resolve {hostname} from here."
        )
    if not edge or edge == ip:
        return _check(
            check_id,
            name,
            started,
            "CDN_NOT_IN_PATH",
            "passed",
            f"{hostname} points straight at the server, so no CDN sits in the path.",
        )
    return check_data_stall(
        edge,
        path,
        server_name=hostname,
        host_header=hostname,
        timeout=timeout,
        check_id=check_id,
        name=name,
        via_cdn=True,
        prober=prober,
    )
