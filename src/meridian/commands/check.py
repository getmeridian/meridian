"""Pre-flight server validation."""

from __future__ import annotations

import shlex
import time

from meridian.commands.resolve import (
    ensure_server_connection,
    fetch_credentials,
    resolve_server,
)
from meridian.config import DEFAULT_SNI, SERVERS_FILE
from meridian.console import err_console, info, line, ok, warn
from meridian.servers import ServerRegistry
from meridian.ssh import tcp_connect


def run(
    ip: str = "",
    domain: str = "",
    sni: str = "",
    user: str = "root",
    ai: bool = False,
    requested_server: str = "",
) -> None:
    """Run pre-flight checks on a server."""
    registry = ServerRegistry(SERVERS_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    resolved = ensure_server_connection(resolved)
    fetch_credentials(resolved)

    err_console.print()
    err_console.print("  [bold]Pre-flight Check[/bold]")
    err_console.print("  [dim]Testing if this server can run a Reality proxy[/dim]")
    err_console.print()

    issues = 0
    sni_host = sni or DEFAULT_SNI
    q_sni = shlex.quote(sni_host)
    results: dict[str, str] = {}

    # --- SNI reachability ---
    info(f"Checking camouflage target ({sni_host}) reachability from server...")
    sni_result = resolved.conn.run(
        f"timeout 5 bash -c 'echo | openssl s_client -connect {q_sni}:443 -servername {q_sni} 2>/dev/null | head -1'",
        timeout=10,
    )
    sni_check = sni_result.stdout.strip()
    if not sni_check:
        # Fallback: TCP connect
        sni_result2 = resolved.conn.run(
            f"nc -z -w 3 {q_sni} 443 2>&1 && echo OK",
            timeout=8,
        )
        sni_check = sni_result2.stdout.strip()

    sni_reachable = any(kw in sni_check for kw in ("CONNECTED", "OK", "Certificate"))
    if sni_reachable:
        ok(f"{sni_host} is reachable from server")
        results["sni"] = "reachable"
    else:
        warn(f"{sni_host} is NOT reachable from server")
        results["sni"] = "NOT reachable"
        issues += 1

    # --- ASN mismatch check ---
    info("Checking camouflage target ASN match...")
    server_org_result = resolved.conn.run("curl -s --max-time 5 https://ipinfo.io/org 2>/dev/null", timeout=10)
    server_org = server_org_result.stdout.strip()

    sni_ip_result = resolved.conn.run(
        f"dig +short {q_sni} @8.8.8.8 2>/dev/null | grep -E '^[0-9]+\\.' | head -1",
        timeout=10,
    )
    sni_ip = sni_ip_result.stdout.strip()

    if server_org and sni_ip and sni_ip[0].isdigit():
        q_sni_ip = shlex.quote(sni_ip)
        sni_org_result = resolved.conn.run(
            f"curl -s --max-time 5 https://ipinfo.io/{q_sni_ip}/org 2>/dev/null",
            timeout=10,
        )
        sni_org = sni_org_result.stdout.strip()
        if sni_org:
            server_asn = server_org.split()[0] if server_org else ""
            sni_asn = sni_org.split()[0] if sni_org else ""
            if server_asn == sni_asn:
                ok(f"Camouflage target is on the same ASN ({server_asn})")
            elif "Apple" in sni_org or "icloud.com" in sni_host or "apple.com" in sni_host:
                warn(f"Camouflage target ({sni_host}) is risky -- Apple infrastructure, detectable ASN mismatch")
                err_console.print("       Use a global CDN domain: www.cloudflare.com, www.twitch.tv, github.com")
                issues += 1
            else:
                info(f"Camouflage target on different ASN (server: {server_org}, target: {sni_org})")
                err_console.print("       Fine for global CDN domains. For best stealth, run: meridian scan")
        else:
            ok("ASN check skipped (could not resolve camouflage target org)")
    else:
        ok("ASN check skipped (ipinfo.io unavailable)")

    # --- Port 443 availability ---
    info("Checking port 443 availability...")
    port_result = resolved.conn.run("ss -tlnp sport = :443 2>/dev/null | grep LISTEN", timeout=10)
    port_check = port_result.stdout.strip()

    if not port_check:
        ok("Port 443 is available")
        results["port443"] = "available"
    else:
        # Extract process name
        import re

        match = re.search(r'users:\(\("([^"]*)"', port_check)
        port_user = match.group(1) if match else "unknown"
        allowed = {"nginx", "3x-ui", "xray"}
        if port_user in allowed:
            ok(f"Port 443 is in use by {port_user} (Meridian -- OK)")
            results["port443"] = f"in use by {port_user} (OK)"
        else:
            warn(f"Port 443 is in use by: {port_user}")
            results["port443"] = f"in use by {port_user}"
            issues += 1

    # --- Port 443 external reachability ---
    info("Checking port 443 external reachability...")
    port443_reachable = tcp_connect(resolved.ip, 443, timeout=5)
    if port443_reachable:
        ok("Port 443 is reachable from outside")
        results["port443_external"] = "reachable"
    else:
        # Check if anything is listening
        listen_result = resolved.conn.run("ss -tlnp sport = :443 2>/dev/null | grep -c LISTEN", timeout=10)
        listen_count = listen_result.stdout.strip()
        if listen_count == "0" or not listen_count:
            ok("Port 443 not yet listening (expected before install)")
            results["port443_external"] = "not yet listening"
        else:
            warn("Port 443 is listening on server but not reachable from outside")
            err_console.print("       Check your cloud provider's firewall / security group settings.")
            err_console.print("       Port 443 (TCP) must be allowed for inbound traffic.")
            results["port443_external"] = "blocked by firewall"
            issues += 1

    # --- Domain DNS ---
    dns_result = ""
    if domain:
        q_domain = shlex.quote(domain)
        info(f"Checking domain DNS ({domain})...")
        dns_r = resolved.conn.run(f"dig +short {q_domain} @8.8.8.8 2>/dev/null", timeout=10)
        dns_result = dns_r.stdout.strip()
        if resolved.ip in dns_result:
            ok(f"{domain} resolves to {resolved.ip}")
        elif dns_result:
            warn(f"{domain} resolves to {dns_result} (expected {resolved.ip})")
            issues += 1
        else:
            warn(f"{domain} does not resolve")
            issues += 1

    # --- Server OS ---
    info("Checking server OS...")
    os_result = resolved.conn.run(
        "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
        timeout=10,
    )
    os_info = os_result.stdout.strip()
    if "Ubuntu" in os_info or "Debian" in os_info:
        # Check for non-LTS Ubuntu (only even-year .04 releases are LTS)
        import re

        ubuntu_ver = re.search(r"Ubuntu (\d+)\.(\d+)", os_info)
        if ubuntu_ver:
            year, month = int(ubuntu_ver.group(1)), int(ubuntu_ver.group(2))
            is_lts = year % 2 == 0 and month == 4
            if not is_lts:
                warn(f"Server OS: {os_info} (non-LTS, may be end-of-life)")
                err_console.print("       Non-LTS Ubuntu releases are supported for only 9 months.")
                err_console.print("       Reinstall with an Ubuntu LTS version for long-term support.")
                issues += 1
            else:
                ok(f"Server OS: {os_info}")
        else:
            ok(f"Server OS: {os_info}")
    elif os_info:
        warn(f"Server OS: {os_info} (tested on Ubuntu/Debian only)")
        issues += 1

    # --- Disk space ---
    info("Checking disk space...")
    disk_result = resolved.conn.run(
        "df -BG / 2>/dev/null | tail -1 | awk '{print $4}' | tr -d 'G'",
        timeout=10,
    )
    disk_avail = disk_result.stdout.strip()
    if disk_avail and disk_avail.isdigit():
        avail_gb = int(disk_avail)
        if avail_gb >= 2:
            ok(f"Disk space: {avail_gb}G available")
        else:
            warn(f"Only {avail_gb}G disk space available (need at least 2G)")
            issues += 1

    # --- Clock sync ---
    info("Checking clock sync...")
    clock_result = resolved.conn.run("date +%s", timeout=10)
    server_epoch_str = clock_result.stdout.strip()
    client_epoch = int(time.time())
    if server_epoch_str and server_epoch_str.isdigit():
        drift = abs(int(server_epoch_str) - client_epoch)
        if drift > 30:
            warn(f"Clock drift: {drift}s between client and server (Reality needs <30s)")
            err_console.print(f"       Fix: ssh root@{resolved.ip} 'timedatectl set-ntp true'")
            err_console.print("       Also check your device's date/time -- enable automatic time.")
            issues += 1
        else:
            ok(f"Clock sync OK ({drift}s drift)")

    # --- Summary ---
    err_console.print()
    line()
    err_console.print()
    if issues == 0:
        err_console.print("  [ok][bold]All checks passed.[/bold][/ok] Server is ready.\n")
        err_console.print(f"  [dim]Next: meridian deploy {resolved.ip}[/dim]")
        err_console.print(f"  [dim]Best SNI: meridian scan {resolved.ip}[/dim]\n")
    else:
        err_console.print(f"  [warn][bold]{issues} issue(s) found.[/bold][/warn] Review the warnings above.\n")
        if not ai:
            err_console.print(f"  [dim]Get AI help: meridian preflight {resolved.ip} --ai[/dim]\n")

    # --- AI mode ---
    if ai:
        from meridian import __version__
        from meridian.ai import build_ai_prompt

        check_lines = [f"Pre-flight Check for {resolved.ip}"]
        check_lines.append(f"Camouflage target ({sni_host}): {results.get('sni', 'unknown')}")
        check_lines.append(f"Port 443: {results.get('port443', 'unknown')}")
        check_lines.append(f"Port 443 external: {results.get('port443_external', 'unknown')}")
        if domain:
            check_lines.append(f"Domain DNS ({domain}): {dns_result or 'no result'}")
        if os_info:
            check_lines.append(f"Server OS: {os_info}")
        if disk_avail and disk_avail.isdigit():
            check_lines.append(f"Disk space: {disk_avail}G available")
        check_lines.append(f"Issues found: {issues}")

        build_ai_prompt("check", "\n".join(check_lines), __version__)
