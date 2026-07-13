"""Reachability test — check proxy accessibility from the client device (no SSH)."""

from __future__ import annotations

import shlex
import subprocess
import time

from meridian.cluster import ClusterConfig
from meridian.commands.resolve import resolve_server
from meridian.config import DEFAULT_SNI, SERVER_PROFILES_FILE
from meridian.console import err_console, info, ok, warn
from meridian.health import tcp_connect
from meridian.servers import ServerRegistry


def run(
    ip: str = "",
    domain: str = "",
    sni: str = "",
    requested_server: str = "",
) -> None:
    """Test proxy reachability from the current device. No SSH required."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip)

    # Load cluster config for node metadata
    cluster = ClusterConfig.load()
    node = cluster.find_node(resolved.ip)
    if node:
        if not domain:
            domain = node.domain or ""
        if not sni:
            sni = node.sni or ""

    sni_host = sni or DEFAULT_SNI
    issues = 0
    checks = 0

    err_console.print("  [bold]Ping[/bold]")
    err_console.print("  [dim]Testing proxy reachability from this device[/dim]")
    err_console.print()

    # 0. Clock check (Reality requires client clock accurate within 30s)
    info("Checking device clock...")
    checks += 1
    try:
        ref_result = subprocess.run(
            ["curl", "-sI", "--max-time", "5", "https://www.google.com/"],
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )
        date_header = ""
        for h_line in ref_result.stdout.splitlines():
            if h_line.lower().startswith("date:"):
                date_header = h_line.split(":", 1)[1].strip()
                break

        client_epoch = int(time.time())
        if date_header:
            ref_epoch = _parse_http_date(date_header)
            if ref_epoch:
                clock_drift = abs(client_epoch - ref_epoch)
                if clock_drift > 30:
                    warn(f"Device clock is off by {clock_drift}s (Reality needs <30s)")
                    err_console.print("       Enable automatic date/time in your device settings.")
                    issues += 1
                else:
                    ok("Device clock OK")
            else:
                ok("Clock check skipped (date parsing unavailable)")
        else:
            ok("Clock check skipped (offline)")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ok("Clock check skipped (offline)")

    # 1. TCP connect to port 443
    info(f"Connecting to {resolved.ip}:443...")
    checks += 1
    if tcp_connect(resolved.ip, 443, timeout=5):
        ok("Port 443 is reachable")
    else:
        warn("Port 443 is not reachable")
        err_console.print("       Possible causes:")
        err_console.print("       - Cloud firewall / security group blocks port 443")
        err_console.print("       - Your ISP or network blocks this server's IP")
        err_console.print("       - Server is down or proxy is not running")
        issues += 1

    # 2. TLS handshake -- Reality should present the SNI target's certificate
    info(f"Checking TLS handshake (Reality -> {sni_host})...")
    checks += 1
    q_sni = shlex.quote(sni_host)
    # openssl -connect requires brackets for IPv6: [2001:db8::1]:443
    connect_host = f"[{resolved.ip}]" if ":" in resolved.ip else resolved.ip
    q_connect = shlex.quote(f"{connect_host}:443")
    try:
        tls_result = subprocess.run(
            ["bash", "-c", f"echo | openssl s_client -connect {q_connect} -servername {q_sni} 2>/dev/null"],
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )
        tls_output = tls_result.stdout
        if "CONNECTED" in tls_output and "Certificate chain" in tls_output:
            # Extract CN
            cert_cn = ""
            for tls_line in tls_output.splitlines():
                if "subject=" in tls_line:
                    parts = tls_line.split("CN = ")
                    if len(parts) > 1:
                        cert_cn = parts[1].strip()
                    break
            suffix = f" (cert: {cert_cn})" if cert_cn else ""
            ok(f"TLS handshake OK{suffix}")
        elif "CONNECTED" in tls_output:
            ok("TLS handshake OK")
        else:
            warn("TLS handshake failed")
            err_console.print("       Reality proxy may not be running, or connection is blocked.")
            issues += 1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        warn("TLS handshake failed (timeout or openssl not found)")
        issues += 1

    # 3. If domain mode, check HTTPS
    if domain:
        info(f"Checking domain https://{domain}/ ...")
        checks += 1
        try:
            http_result = subprocess.run(
                [
                    "curl",
                    "-sSf",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "--connect-timeout",
                    "5",
                    "--max-time",
                    "10",
                    f"https://{domain}/",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
            http_code = http_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            http_code = "000"

        if http_code and http_code[0] in ("2", "3"):
            ok(f"Domain is responding (HTTP {http_code})")
        elif http_code == "000":
            warn("Domain is not reachable")
            err_console.print("       Check DNS settings and that nginx is running on the server.")
            issues += 1
        else:
            warn(f"Domain returned HTTP {http_code}")
            issues += 1

    # 4. Relay reachability checks
    for relay in cluster.relays:
        # Only test relays that forward to this node
        if relay.exit_node_ip and relay.exit_node_ip != resolved.ip:
            continue
        relay_label = relay.name or relay.ip
        info(f"Connecting to relay {relay_label} ({relay.ip}:{relay.port})...")
        checks += 1
        if tcp_connect(relay.ip, relay.port, timeout=5):
            ok(f"Relay {relay_label} is reachable")
        else:
            warn(f"Relay {relay_label} ({relay.ip}:{relay.port}) is not reachable")
            issues += 1

    # Summary
    err_console.print()
    if issues == 0:
        err_console.print(f"  [ok]All {checks} checks passed.[/ok] Server is reachable from this device.")
    else:
        err_console.print(
            f"  [error]{issues} issue(s) found.[/error] The server is not fully reachable from this device."
        )
        err_console.print("  [dim]This means the problem is between your device and the server,[/dim]")
        err_console.print("  [dim]not with the Meridian deployment itself.[/dim]")
        err_console.print()
        err_console.print("  [dim]Common fixes:[/dim]")
        err_console.print("  [dim]  - Open port 443/TCP in your cloud provider's firewall[/dim]")
        err_console.print("  [dim]  - Try from a different network (mobile data, another Wi-Fi)[/dim]")
        err_console.print("  [dim]  - Your ISP may be blocking this server's IP -- try a different server[/dim]")

    # --- Connection tests (actual traffic through proxy) ---
    if issues == 0 and node and node.reality_public_key:
        _run_connection_tests(cluster, resolved.ip)

    err_console.print()


def _run_connection_tests(cluster: ClusterConfig, server_ip: str) -> None:
    """Test actual proxy connections using a local xray client."""
    from meridian.xray_client import build_test_configs_from_cluster, ensure_xray_binary, test_connection

    err_console.print()
    err_console.print("  [bold]Connection[/bold]")
    err_console.print("  [dim]Testing actual proxy traffic through this device[/dim]")
    err_console.print()

    info("Checking xray client binary...")
    xray_bin = ensure_xray_binary()
    if xray_bin is None:
        warn("Could not download xray client — connection tests skipped")
        err_console.print("       [dim]Check your internet connection and try again.[/dim]")
        return
    ok(f"xray ready ({xray_bin.name})")

    # Fetch a real client UUID from the panel (dummy UUID won't pass Remnawave auth)
    test_uuid = ""
    if cluster.panel.url and cluster.panel.api_token:
        try:
            from meridian.remnawave import MeridianPanel

            with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
                users = panel.list_users()
                if users:
                    test_uuid = users[0].vless_uuid or users[0].uuid
        except (OSError, RuntimeError):
            pass  # Fall back to dummy UUID (will test reachability only)
    if not test_uuid:
        warn("No client UUID available — connection tests may fail auth")

    configs = build_test_configs_from_cluster(cluster, server_ip, uuid=test_uuid)
    if not configs:
        warn("No active protocols found in cluster config")
        return

    passed = 0
    failed = 0
    for label, config, expect_ip_match in configs:
        socks_port = config["inbounds"][0]["port"]
        info(f"Testing {label}...")
        success, detail = test_connection(xray_bin, config, server_ip, socks_port, label, expect_ip_match)
        if success:
            ok(f"{label} OK — {detail}")
            passed += 1
        else:
            warn(f"{label} failed — {detail}")
            failed += 1

    err_console.print()
    if failed == 0:
        err_console.print(f"  [ok]All {passed} protocol(s) verified.[/ok] Proxy is fully operational.")
    else:
        err_console.print(f"  [error]{failed} protocol(s) failed, {passed} passed.[/error]")


def _parse_http_date(date_str: str) -> int | None:
    """Parse an HTTP Date header into epoch seconds."""
    import calendar
    import email.utils

    try:
        parsed = email.utils.parsedate(date_str)
        if parsed:
            return int(calendar.timegm(parsed))
    except (TypeError, ValueError, OverflowError):
        pass
    return None
