"""SNI scanner — find optimal Reality SNI targets on the server's network."""

from __future__ import annotations

import csv
import io
import ipaddress
import shlex

import typer

from meridian.commands._helpers import load_cluster
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import (
    REALITL_SCANNER_ASSETS,
    REALITL_SCANNER_GITHUB_URL,
    REALITL_SCANNER_VERSION,
    SERVER_PROFILES_FILE,
)
from meridian.console import err_console, fail, info, line, ok, prompt, warn
from meridian.core.inputs import validate_hostname_value
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection, SSHError


class ScanUnavailableError(RuntimeError):
    """The scanner could not collect enough evidence to complete."""


def _unavailable(message: str, *, strict: bool) -> list[str]:
    if strict:
        raise ScanUnavailableError(message)
    warn(message)
    return []


def scan_for_sni(conn: ServerConnection, ip: str, *, strict: bool = False) -> list[str]:
    """Run RealiTLScanner on the server and return list of discovered SNI targets.

    Returns an empty list for operational failures in optional mode and when a
    completed scan finds no candidates. Strict mode distinguishes operational
    failures by raising ``ScanUnavailableError``.
    Does NOT prompt the user -- just returns the candidates.
    """
    # Detect server architecture
    try:
        arch_result = conn.run("uname -m", timeout=10)
    except (OSError, SSHError):
        return _unavailable("Could not determine the server architecture", strict=strict)
    raw_arch = arch_result.stdout.strip()
    if arch_result.returncode != 0 or not raw_arch:
        return _unavailable("Could not determine the server architecture", strict=strict)
    asset = REALITL_SCANNER_ASSETS.get(raw_arch)
    if asset is None:
        return _unavailable(f"Unsupported architecture for scanner: {raw_arch}", strict=strict)
    asset_name, expected_sha256 = asset
    scanner_url = f"{REALITL_SCANNER_GITHUB_URL}/v{REALITL_SCANNER_VERSION}/{asset_name}"
    q_url = shlex.quote(scanner_url)

    # Get server's subnet CIDR for scanning
    try:
        # Filter out private/loopback IPs: 127.x, 10.x, 172.16-31.x, 192.168.x
        private_filter = r"127\.\|10\.\|172\.1[6-9]\.\|172\.2[0-9]\.\|172\.3[01]\.\|192\.168\."
        q_private_filter = shlex.quote(private_filter)
        cidr_result = conn.run(
            f"ip addr show | grep 'inet ' | grep -v {q_private_filter} | head -1 | awk '{{print $2}}'",
            timeout=10,
        )
        server_cidr = cidr_result.stdout.strip()
    except (OSError, SSHError):
        server_cidr = ""
    try:
        network = ipaddress.ip_interface(server_cidr).network if server_cidr else None
    except ValueError:
        network = None
    if network is None or network.version != 4:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return _unavailable("Scanner target is not a valid IP address", strict=strict)
        if address.version != 4:
            return _unavailable("RealiTLScanner currently requires an IPv4 target", strict=strict)
        network = ipaddress.ip_network(f"{address}/24", strict=False)
    server_cidr = str(network)

    # Download, verify, execute, and clean up in one isolated remote directory.
    q_cidr = shlex.quote(server_cidr)
    q_sha256 = shlex.quote(expected_sha256)
    command = (
        "set -eu; tmpdir=$(mktemp -d /tmp/meridian-scan.XXXXXX); "
        "trap 'rm -rf \"$tmpdir\"' EXIT; "
        f'curl -sSfL --max-time 30 -o "$tmpdir/scanner" {q_url} </dev/null; '
        f"printf '%s  %s\\n' {q_sha256} \"$tmpdir/scanner\" | sha256sum -c - >/dev/null; "
        'chmod 700 "$tmpdir/scanner"; '
        f'timeout 90 "$tmpdir/scanner" -addr {q_cidr} '
        '-out "$tmpdir/results.csv" -thread 4 -timeout 5 >/dev/null 2>&1; '
        'cat "$tmpdir/results.csv"'
    )
    from rich.status import Status

    with Status(f"  [cyan]\u2192 Scanning {server_cidr}...[/cyan]", console=err_console, spinner="dots"):
        try:
            scan_result = conn.run(command, timeout=130)
        except (OSError, SSHError):
            return _unavailable("Scanner connection failed or timed out", strict=strict)
    if scan_result.returncode != 0:
        return _unavailable(
            "RealiTLScanner download, checksum verification, or scan failed",
            strict=strict,
        )
    csv_output = scan_result.stdout.strip()

    if not csv_output or csv_output == "IP,ORIGIN,CERT_DOMAIN,CERT_ISSUER,GEO_CODE":
        return []

    # Parse CSV: extract cert_domain (column 3), skip header, deduplicate, filter bad targets
    domains: list[str] = []
    for parts in csv.reader(io.StringIO(csv_output)):
        if len(parts) < 3:
            continue
        csv_ip, _origin, cert_domain = parts[0], parts[1], parts[2]
        if csv_ip == "IP":
            continue  # header
        cert_domain = cert_domain.strip()
        if not cert_domain or cert_domain.startswith("*"):
            continue  # wildcard certs
        try:
            cert_domain = validate_hostname_value(cert_domain)
        except ValueError:
            continue
        # Filter known-bad targets
        if any(
            bad in cert_domain.lower()
            for bad in (
                "apple.com",
                "icloud.com",  # ASN mismatch with VPS providers
                "fake",
                "kubernetes",
                "ingress",  # self-signed / k8s default certs
                "localhost",
                "invalid",
                "example",  # placeholder certs
            )
        ):
            continue
        # Skip the server's own IP in domain
        if ip in cert_domain:
            continue
        # Deduplicate
        if cert_domain not in domains:
            domains.append(cert_domain)

    return domains


def run(
    ip: str = "",
    user: str = "root",
    requested_server: str = "",
) -> None:
    """Download RealiTLScanner, scan the server's subnet, and let user pick an SNI target."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    resolved = ensure_server_connection(resolved)

    err_console.print()
    err_console.print("  [bold]SNI Scanner[/bold]")
    err_console.print("  [dim]Finding optimal Reality SNI targets near your server...[/dim]")
    err_console.print()

    try:
        domains = scan_for_sni(resolved.conn, resolved.ip, strict=True)
    except ScanUnavailableError as exc:
        fail(
            str(exc),
            hint="Check the server's network access and retry, or provide --sni during setup.",
            hint_type="system",
        )

    if not domains:
        warn("No suitable SNI targets found.")
        err_console.print(f"\n  [dim]You can manually set SNI with: meridian deploy {resolved.ip} --sni DOMAIN[/dim]\n")
        raise typer.Exit(4)

    err_console.print()
    line()
    err_console.print()
    err_console.print(f"  [bold]Found {len(domains)} SNI targets:[/bold]\n")

    for i, domain in enumerate(domains, 1):
        err_console.print(f"    [info]{i}[/info]) {domain}")

    err_console.print()
    choice = prompt(f"Select a target (1-{len(domains)}) or press Enter to skip")

    if choice:
        if not choice.isdigit():
            warn(f"Enter a number from 1 to {len(domains)}, or press Enter to skip")
            raise typer.Exit(2)
        idx = int(choice)
        if 1 <= idx <= len(domains):
            selected = domains[idx - 1]

            # Legacy nodes can store the selection directly. V4 intent is a
            # reviewed contract and must only change through setup/apply.
            cluster = load_cluster(require_configured=False)
            saved = False
            if cluster.topology_intent is not None:
                warn("V4 topology intent is review-managed; the selected SNI was not saved")
            elif (node := cluster.find_node(resolved.ip)) is not None:
                node.sni = selected
                cluster.save()
                saved = True
            else:
                warn(f"Node {resolved.ip} not found in cluster.yml; SNI was not saved")

            err_console.print()
            ok(f"{'Saved' if saved else 'Selected'}: {selected}")
            if cluster.topology_intent is not None:
                err_console.print("  [dim]Update the reviewed Reality SNI through: meridian setup[/dim]")
            else:
                err_console.print(f"  [dim]Use it: meridian deploy {resolved.ip} --sni {selected}[/dim]")
            if saved:
                err_console.print("  [dim]Or it will be suggested automatically during deploy.[/dim]")
            err_console.print("  [dim]Tip: with --domain, your own domain works as SNI too (self-steal).[/dim]\n")
            return

        warn(f"Enter a number from 1 to {len(domains)}, or press Enter to skip")
        raise typer.Exit(2)

    info("No target selected. The default (www.microsoft.com) works well for most servers.")
    err_console.print()
