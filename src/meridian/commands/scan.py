"""SNI scanner — find optimal Reality SNI targets on the server's network."""

from __future__ import annotations

import shlex

from meridian.commands.resolve import (
    ensure_server_connection,
    fetch_credentials,
    resolve_server,
)
from meridian.config import SERVERS_FILE
from meridian.console import err_console, info, line, ok, prompt, warn
from meridian.credentials import ServerCredentials
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection


def scan_for_sni(conn: ServerConnection, ip: str) -> list[str]:
    """Run RealiTLScanner on the server and return list of discovered SNI targets.

    Returns empty list if scan fails or no results found.
    Does NOT prompt the user -- just returns the candidates.
    """
    # Detect server architecture
    try:
        arch_result = conn.run("uname -m", timeout=10)
    except Exception:
        return []
    raw_arch = arch_result.stdout.strip()
    match raw_arch:
        case "x86_64":
            arch = "amd64"
        case "aarch64":
            arch = "arm64"
        case _:
            warn(f"Unsupported architecture for scanner: {raw_arch}")
            return []

    # Download RealiTLScanner to server
    scanner_url = f"https://github.com/XTLS/RealiTLScanner/releases/latest/download/RealiTLScanner-linux-{arch}"
    q_url = shlex.quote(scanner_url)
    from rich.status import Status

    with Status("  [cyan]\u2192 Downloading RealiTLScanner...[/cyan]", console=err_console, spinner="dots"):
        try:
            dl_result = conn.run(
                f"curl -sSfL --max-time 30 -o /tmp/realitlscanner {q_url} </dev/null && chmod +x /tmp/realitlscanner",
                timeout=40,
            )
        except Exception:
            pass
            dl_result = None
    if not dl_result or dl_result.returncode != 0:
        warn("Failed to download RealiTLScanner")
        return []

    # Verify downloaded binary integrity (ELF header + minimum size)
    try:
        verify_result = conn.run(
            "file /tmp/realitlscanner | grep -q 'ELF.*executable' && "
            "test $(stat -c%s /tmp/realitlscanner 2>/dev/null || stat -f%z /tmp/realitlscanner) -gt 100000",
            timeout=10,
        )
        if verify_result.returncode != 0:
            warn("Downloaded scanner binary failed integrity check")
            conn.run("rm -f /tmp/realitlscanner", timeout=5)
            return []
    except Exception:
        pass  # verification is best-effort

    # Get server's subnet CIDR for scanning
    try:
        # Filter out private/loopback IPs: 127.x, 10.x, 172.16-31.x, 192.168.x
        private_filter = r"127\.\|10\.\|172\.1[6-9]\.\|172\.2[0-9]\.\|172\.3[01]\.\|192\.168\."
        cidr_result = conn.run(
            f"ip addr show | grep 'inet ' | grep -v '{private_filter}' | head -1 | awk '{{print $2}}'",
            timeout=10,
        )
        server_cidr = cidr_result.stdout.strip()
    except Exception:
        server_cidr = ""
    if not server_cidr:
        server_cidr = f"{ip}/24"

    # Run scan
    q_cidr = shlex.quote(server_cidr)
    with Status(f"  [cyan]\u2192 Scanning {server_cidr}...[/cyan]", console=err_console, spinner="dots"):
        try:
            conn.run(
                f"cd /tmp && timeout 90 ./realitlscanner -addr {q_cidr}"
                " -out /tmp/meridian-scan.csv -thread 4 -timeout 5 >/dev/null 2>&1",
                timeout=100,
            )
        except Exception:
            warn("Scan timed out")
            conn.run("rm -f /tmp/realitlscanner /tmp/meridian-scan.csv", timeout=5)
            return []

    # Clean up binary, read CSV, clean up CSV -- all in one SSH call
    try:
        csv_result = conn.run(
            "cat /tmp/meridian-scan.csv 2>/dev/null; rm -f /tmp/realitlscanner /tmp/meridian-scan.csv",
            timeout=10,
        )
    except Exception:
        return []
    csv_output = csv_result.stdout.strip()

    if not csv_output or csv_output == "IP,ORIGIN,CERT_DOMAIN,CERT_ISSUER,GEO_CODE":
        return []

    # Parse CSV: extract cert_domain (column 3), skip header, deduplicate, filter bad targets
    domains: list[str] = []
    for csv_line in csv_output.splitlines():
        parts = csv_line.split(",")
        if len(parts) < 3:
            continue
        csv_ip, _origin, cert_domain = parts[0], parts[1], parts[2]
        if csv_ip == "IP":
            continue  # header
        if not cert_domain:
            continue
        if cert_domain.startswith("*"):
            continue  # wildcard certs
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
    registry = ServerRegistry(SERVERS_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    resolved = ensure_server_connection(resolved)
    fetch_credentials(resolved)

    err_console.print()
    err_console.print("  [bold]SNI Scanner[/bold]")
    err_console.print("  [dim]Finding optimal Reality SNI targets near your server...[/dim]")
    err_console.print()

    domains = scan_for_sni(resolved.conn, resolved.ip)

    if not domains:
        warn("No suitable SNI targets found.")
        err_console.print(f"\n  [dim]You can manually set SNI with: meridian deploy {resolved.ip} --sni DOMAIN[/dim]\n")
        return

    err_console.print()
    line()
    err_console.print()
    err_console.print(f"  [bold]Found {len(domains)} SNI targets:[/bold]\n")

    for i, domain in enumerate(domains, 1):
        err_console.print(f"    [info]{i}[/info]) {domain}")

    err_console.print()
    choice = prompt(f"Select a target (1-{len(domains)}) or press Enter to skip")

    if choice and choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(domains):
            selected = domains[idx - 1]

            # Save to credentials
            resolved.creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            proxy_file = resolved.creds_dir / "proxy.yml"
            creds = ServerCredentials.load(proxy_file)
            creds.server.scanned_sni = selected
            creds.save(proxy_file)

            err_console.print()
            ok(f"Saved: {selected}")
            err_console.print(f"  [dim]Use it: meridian deploy {resolved.ip} --sni {selected}[/dim]")
            err_console.print("  [dim]Or it will be suggested automatically during deploy.[/dim]")
            err_console.print("  [dim]Tip: with --domain, your own domain works as SNI too (self-steal).[/dim]\n")
            return

    info("No target selected. The default (www.microsoft.com) works well for most servers.")
    err_console.print()
