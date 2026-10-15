"""Find Reality camouflage sites that live next to the server.

A Reality connection shows the censor an SNI and the server's IP. When the SNI is
a site hosted in the same subnet, its DNS really points next door, so the pair
looks like ordinary traffic; a famous foreign site on a VPS address does not.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import shlex
from dataclasses import dataclass
from typing import Literal

from meridian.config import REALITL_SCANNER_ASSETS, REALITL_SCANNER_GITHUB_URL, REALITL_SCANNER_VERSION
from meridian.core.errors import MeridianError
from meridian.core.execution import ServerConnection
from meridian.core.inputs import validate_hostname_value
from meridian.diagnostics.preflight import reachable_reality_targets

# Certificates that never make a usable target: ASN-mismatched Apple names, and
# placeholder or self-signed defaults from fake, Kubernetes, or example setups.
_BAD_NAME_PARTS = ("apple.com", "icloud.com", "fake", "kubernetes", "ingress", "localhost", "invalid", "example")
# Bounds the DNS and TLS checks; a /24 rarely yields more usable names than this.
_MAX_CHECKED = 8


def camouflage_refusal(sni: str) -> Literal["", "network", "certificate"]:
    """Why a name can never be a Reality camouflage target, or "" when nothing rules it out.

    ``network``: Apple's sites live in Apple's own network, which no hosting provider's
    address matches. ``certificate``: www.microsoft.com sends a certificate larger than
    Reality's 8192-byte limit, so no traffic passes.
    """
    name = sni.strip().lower().rstrip(".")
    if any(name == domain or name.endswith(f".{domain}") for domain in ("apple.com", "icloud.com")):
        return "network"
    if name in {"microsoft.com", "www.microsoft.com"}:
        return "certificate"
    return ""


class ScanUnavailableError(MeridianError):
    """The scanner could not collect enough evidence to complete."""


@dataclass(frozen=True)
class NeighborScan:
    network: ipaddress.IPv4Network
    domains: list[str]


def scan_neighbors(conn: ServerConnection, ip: str) -> NeighborScan:
    """Run the pinned RealiTLScanner over the server's IPv4 subnet and list certificate names.

    An empty list means a completed scan found nothing; operational failures raise
    ``ScanUnavailableError``.
    """
    try:
        arch_result = conn.run("uname -m", timeout=10)
    except (OSError, MeridianError) as exc:
        raise ScanUnavailableError("Could not determine the server architecture") from exc
    raw_arch = arch_result.stdout.strip()
    if arch_result.returncode != 0 or not raw_arch:
        raise ScanUnavailableError("Could not determine the server architecture")
    asset = REALITL_SCANNER_ASSETS.get(raw_arch)
    if asset is None:
        raise ScanUnavailableError(f"Unsupported architecture for scanner: {raw_arch}")
    asset_name, expected_sha256 = asset
    scanner_url = f"{REALITL_SCANNER_GITHUB_URL}/v{REALITL_SCANNER_VERSION}/{asset_name}"
    network = _server_network(conn, ip)

    # Download, verify, execute, and clean up in one isolated remote directory.
    command = (
        "set -eu; tmpdir=$(mktemp -d /tmp/meridian-scan.XXXXXX); "
        "trap 'rm -rf \"$tmpdir\"' EXIT; "
        f'curl -sSfL --max-time 30 -o "$tmpdir/scanner" {shlex.quote(scanner_url)} </dev/null; '
        f"printf '%s  %s\\n' {shlex.quote(expected_sha256)} \"$tmpdir/scanner\" | sha256sum -c - >/dev/null; "
        'chmod 700 "$tmpdir/scanner"; '
        f'timeout 90 "$tmpdir/scanner" -addr {shlex.quote(str(network))} '
        '-out "$tmpdir/results.csv" -thread 4 -timeout 5 >/dev/null 2>&1; '
        'cat "$tmpdir/results.csv"'
    )
    try:
        scan_result = conn.run(command, timeout=130)
    except (OSError, MeridianError) as exc:
        raise ScanUnavailableError("Scanner connection failed or timed out") from exc
    if scan_result.returncode != 0:
        raise ScanUnavailableError("RealiTLScanner download, checksum verification, or scan failed")
    return NeighborScan(network=network, domains=_parse_domains(scan_result.stdout.strip(), ip))


def neighbor_camouflage_targets(conn: ServerConnection, ip: str, *, limit: int = 2) -> list[str]:
    """Up to *limit* same-subnet sites whose DNS points into the subnet and that pass Reality's TLS check.

    Returns an empty list when the scan is unavailable; callers fall back to known targets.
    """
    try:
        scan = scan_neighbors(conn, ip)
        local = [domain for domain in scan.domains[:_MAX_CHECKED] if _resolves_into(conn, domain, scan.network)]
        return reachable_reality_targets(conn, tuple(local))[:limit]
    except (OSError, MeridianError):
        return []


def _server_network(conn: ServerConnection, ip: str) -> ipaddress.IPv4Network:
    # The first public IPv4 interface address; private and loopback ranges are skipped.
    private_filter = r"127\.\|10\.\|172\.1[6-9]\.\|172\.2[0-9]\.\|172\.3[01]\.\|192\.168\."
    try:
        cidr_result = conn.run(
            f"ip addr show | grep 'inet ' | grep -v {shlex.quote(private_filter)} | head -1 | awk '{{print $2}}'",
            timeout=10,
        )
        server_cidr = cidr_result.stdout.strip()
    except (OSError, MeridianError):
        server_cidr = ""
    try:
        network = ipaddress.ip_interface(server_cidr).network if server_cidr else None
    except ValueError:
        network = None
    if isinstance(network, ipaddress.IPv4Network):
        # Cloud VPSes (Hetzner, others) get a /32 point-to-point address; their neighbours share the /24.
        return network.supernet(new_prefix=24) if network.prefixlen > 24 else network
    try:
        address = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ScanUnavailableError("Scanner target is not a valid IP address") from exc
    if address.version != 4:
        raise ScanUnavailableError("RealiTLScanner currently requires an IPv4 target")
    return ipaddress.IPv4Network(f"{address}/24", strict=False)


def _parse_domains(csv_output: str, ip: str) -> list[str]:
    if not csv_output:
        return []
    # The column set has changed between scanner releases (0.2.3 writes TLS,
    # ALPN, curve and certificate details before CERT_DOMAIN). Read columns by
    # name so a format change can never smuggle e.g. "TLS 1.3" into serverNames.
    reader = csv.DictReader(io.StringIO(csv_output))
    if not {"IP", "CERT_DOMAIN"} <= set(reader.fieldnames or ()):
        raise ScanUnavailableError("RealiTLScanner output has an unexpected format")
    domains: list[str] = []
    for row in reader:
        cert_domain = (row.get("CERT_DOMAIN") or "").strip()
        if not cert_domain or cert_domain.startswith("*"):
            continue  # wildcard certs
        try:
            cert_domain = validate_hostname_value(cert_domain)
        except ValueError:
            continue
        if any(bad in cert_domain.lower() for bad in _BAD_NAME_PARTS) or ip in cert_domain:
            continue
        if cert_domain not in domains:
            domains.append(cert_domain)
    return domains


def _resolves_into(conn: ServerConnection, domain: str, network: ipaddress.IPv4Network) -> bool:
    result = conn.run(f"getent ahostsv4 {shlex.quote(domain)}", timeout=10)
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        try:
            if ipaddress.ip_address(line.split()[0]) in network:
                return True
        except (IndexError, ValueError):
            continue
    return False
