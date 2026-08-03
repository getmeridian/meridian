"""Pre-flight server validation."""

from __future__ import annotations

import ipaddress
import shlex
import time

import typer

from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
from meridian.console import err_console, fail, info, line, ok, warn
from meridian.core.inputs import validate_hostname_value
from meridian.diagnostics import command_evidence_unavailable
from meridian.diagnostics.network import resolve_hostname
from meridian.facts import ServerFacts
from meridian.health import tcp_connect
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection, SSHError


def _probe_unused_external_port(conn: ServerConnection, ip: str) -> bool | None:
    """Temporarily listen without serving content so firewall reachability is testable."""
    if ipaddress.ip_address(ip).version == 6:
        script = (
            "import socket,time; s=socket.socket(socket.AF_INET6,socket.SOCK_STREAM); "
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
            "s.bind(('::',443)); s.listen(1); time.sleep(10)"
        )
    else:
        script = (
            "import socket,time; s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); "
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
            "s.bind(('0.0.0.0',443)); s.listen(1); time.sleep(10)"
        )
    q_script = shlex.quote(script)
    try:
        started = conn.run(
            "command -v python3 >/dev/null 2>&1 || exit 127; "
            f"nohup python3 -c {q_script} </dev/null >/dev/null 2>&1 & echo $!",
            timeout=10,
        )
        pid = started.stdout.strip().splitlines()[-1] if started.stdout.strip() else ""
        if started.returncode != 0 or not pid.isdigit():
            return None
        q_pid = shlex.quote(pid)
        time.sleep(0.25)
        alive = conn.run(f"kill -0 {q_pid} 2>/dev/null", timeout=5)
        if alive.returncode != 0:
            return None
        try:
            return tcp_connect(ip, 443, timeout=5)
        finally:
            conn.run(f"kill {q_pid} 2>/dev/null || true", timeout=5)
    except (OSError, SSHError):
        return None


def run(
    ip: str = "",
    domain: str = "",
    sni: str = "",
    user: str = "root",
    ai: bool = False,
    requested_server: str = "",
) -> None:
    """Run pre-flight checks on a server."""
    try:
        domain = validate_hostname_value(domain) if domain else ""
        sni = validate_hostname_value(sni) if sni else ""
    except ValueError as exc:
        fail(str(exc), hint_type="user")
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    try:
        probe_ip = str(ipaddress.ip_address(resolved.ip))
    except ValueError:
        resolved_probe_ip = resolve_hostname(resolved.ip, timeout=5)
        if not resolved_probe_ip:
            fail(
                f"Could not resolve server hostname '{resolved.ip}'",
                hint="Check local DNS or pass the server's public IP explicitly.",
                hint_type="system",
            )
        probe_ip = resolved_probe_ip

    resolved = ensure_server_connection(resolved)

    err_console.print()
    err_console.print("  [bold]Pre-flight Check[/bold]")
    err_console.print("  [dim]Testing if this server can run a Reality proxy[/dim]")
    err_console.print()

    issues = 0
    unavailable = 0
    sni_host = sni or DEFAULT_SNI
    q_sni = shlex.quote(sni_host)
    results: dict[str, str] = {}
    facts = ServerFacts(resolved.conn)

    # --- SNI reachability ---
    info(f"Checking camouflage target ({sni_host}) reachability from server...")
    q_sni_endpoint = shlex.quote(f"{sni_host}:443")
    sni_result = resolved.conn.run(
        "command -v openssl >/dev/null 2>&1 || exit 127; "
        f"timeout 5 openssl s_client -connect {q_sni_endpoint} -servername {q_sni} "
        "</dev/null 2>/dev/null | head -1",
        timeout=10,
    )
    sni_check = sni_result.stdout.strip()
    if not sni_check:
        # Fallback: TCP connect
        sni_result2 = resolved.conn.run(
            f"command -v nc >/dev/null 2>&1 || exit 127; nc -z -w 3 {q_sni} 443 2>&1 && echo OK",
            timeout=8,
        )
        sni_check = sni_result2.stdout.strip()
    else:
        sni_result2 = None

    sni_reachable = any(kw in sni_check for kw in ("CONNECTED", "OK", "Certificate"))
    if sni_reachable:
        ok(f"{sni_host} is reachable from server")
        results["sni"] = "reachable"
    elif any(result.returncode in {124, 255} for result in (sni_result, sni_result2) if result is not None) or (
        sni_result.returncode == 127 and (sni_result2 is None or sni_result2.returncode == 127)
    ):
        warn("Could not test camouflage target reachability (openssl and nc are unavailable)")
        results["sni"] = "evidence unavailable"
        unavailable += 1
    else:
        warn(f"{sni_host} is NOT reachable from server")
        results["sni"] = "NOT reachable"
        issues += 1

    # Use the server's resolver without disclosing targets to a third party.
    sni_ip_result = resolved.conn.run(
        "command -v getent >/dev/null 2>&1 || exit 127; "
        f"getent ahostsv4 {q_sni} 2>/dev/null | awk 'NR == 1 {{print $1}}'",
        timeout=10,
    )
    sni_ip = sni_ip_result.stdout.strip()

    if sni_ip:
        ok(f"Camouflage target resolves from the server ({sni_ip})")
    elif command_evidence_unavailable(sni_ip_result.returncode):
        warn("Could not test camouflage target DNS through the server resolver")
        unavailable += 1
    else:
        warn("Camouflage target did not resolve through the server's configured DNS")
        issues += 1

    # --- Port 443 availability ---
    info("Checking port 443 availability...")
    port_result = resolved.conn.run("ss -H -tlnp sport = :443 2>/dev/null", timeout=10)
    port_check = port_result.stdout.strip()

    if port_result.returncode != 0:
        warn("Could not inspect port 443 listeners (ss is unavailable or failed)")
        results["port443"] = "evidence unavailable"
        unavailable += 1
    elif not port_check:
        ok("Port 443 is available")
        results["port443"] = "available"
    else:
        # Extract process name
        import re

        match = re.search(r'users:\(\("([^"]*)"', port_check)
        port_user = match.group(1) if match else "unknown"
        allowed = {"nginx", "xray", "remnawave", "remnawave-node", "docker-proxy"}
        ownership = resolved.conn.run(
            "test -f /etc/meridian/node.yml && echo managed",
            timeout=10,
        )
        if command_evidence_unavailable(ownership.returncode):
            warn("Could not verify whether the port 443 listener belongs to Meridian")
            results["port443"] = "ownership evidence unavailable"
            unavailable += 1
        elif port_user in allowed and ownership.returncode == 0 and ownership.stdout.strip() == "managed":
            ok(f"Port 443 is in use by {port_user} (Meridian -- OK)")
            results["port443"] = f"in use by {port_user} (OK)"
        else:
            warn(f"Port 443 is in use by: {port_user}")
            results["port443"] = f"in use by {port_user}"
            issues += 1

    # --- Port 443 external reachability ---
    info("Checking port 443 external reachability...")
    local_vantage = bool(getattr(resolved, "local_mode", False))
    if local_vantage:
        port443_reachable = None
    elif port_result.returncode == 0 and not port_check:
        port443_reachable = _probe_unused_external_port(resolved.conn, probe_ip)
    else:
        port443_reachable = tcp_connect(probe_ip, 443, timeout=5)
    if port443_reachable is True:
        ok("Port 443 is reachable from outside")
        results["port443_external"] = "reachable"
    elif port443_reachable is None:
        if local_vantage:
            warn("External port 443 reachability needs a separate network vantage in local mode")
        else:
            warn("Could not create a temporary listener to test external port 443 reachability")
        results["port443_external"] = "evidence unavailable"
        unavailable += 1
    elif port_result.returncode != 0:
        warn("Port 443 is not externally reachable, and local listener evidence is unavailable")
        results["port443_external"] = "evidence unavailable"
        unavailable += 1
    elif not port_check:
        warn("Temporary port 443 listener was not reachable from outside")
        err_console.print("       Check your cloud provider's firewall / security group settings.")
        results["port443_external"] = "blocked by firewall"
        issues += 1
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
        dns_r = resolved.conn.run(
            "command -v getent >/dev/null 2>&1 || exit 127; "
            f"getent ahosts {q_domain} 2>/dev/null | awk '{{print $1}}' | sort -u",
            timeout=10,
        )
        dns_result = dns_r.stdout.strip()
        resolved_addresses: set[str] = set()
        for candidate in dns_result.splitlines():
            try:
                resolved_addresses.add(str(ipaddress.ip_address(candidate.strip())))
            except ValueError:
                continue
        expected_address = probe_ip
        if command_evidence_unavailable(dns_r.returncode):
            warn(f"Could not test domain DNS for {domain} through the server resolver")
            unavailable += 1
        elif expected_address in resolved_addresses:
            ok(f"{domain} resolves to {resolved.ip}")
        elif resolved_addresses:
            rendered_addresses = ", ".join(sorted(resolved_addresses))
            warn(f"{domain} resolves to {rendered_addresses} (expected {resolved.ip})")
            issues += 1
        else:
            warn(f"{domain} does not resolve")
            issues += 1

    # --- Server OS ---
    info("Checking server OS...")
    os_release = facts.os_release()
    os_info = os_release.pretty_name or os_release.id
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
    else:
        warn("Could not determine the server OS")
        unavailable += 1

    # --- Disk space ---
    info("Checking disk space...")
    disk_mb = facts.free_disk_mb("/")
    disk_avail = ""
    if disk_mb is not None:
        avail_gb = disk_mb // 1024
        disk_avail = str(avail_gb)
        if avail_gb >= 2:
            ok(f"Disk space: {avail_gb}G available")
        else:
            warn(f"Only {avail_gb}G disk space available (need at least 2G)")
            issues += 1
    else:
        warn("Could not determine available disk space")
        unavailable += 1

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
    else:
        warn("Could not determine server clock drift")
        unavailable += 1

    # --- Summary ---
    err_console.print()
    line()
    err_console.print()
    if issues == 0 and unavailable == 0:
        err_console.print("  [ok][bold]All checks passed.[/bold][/ok] Server is ready.\n")
        err_console.print(f"  [dim]Next: meridian deploy {resolved.ip}[/dim]")
        err_console.print(f"  [dim]Best SNI: meridian scan {resolved.ip}[/dim]\n")
    elif issues == 0:
        err_console.print(
            f"  [warn][bold]{unavailable} check(s) inconclusive.[/bold][/warn] Required evidence was unavailable.\n"
        )
    else:
        err_console.print(f"  [warn][bold]{issues} issue(s) found.[/bold][/warn] Review the warnings above.\n")
        if unavailable:
            err_console.print(f"  [warn]{unavailable} additional check(s) were inconclusive.[/warn]\n")
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
        check_lines.append(f"Checks inconclusive: {unavailable}")

        build_ai_prompt("check", "\n".join(check_lines), __version__)

    if issues:
        raise typer.Exit(4)
    if unavailable:
        raise typer.Exit(3)
