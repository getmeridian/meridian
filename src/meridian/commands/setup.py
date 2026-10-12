"""Deploy proxy server — interactive wizard and provisioner execution."""

from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path

from meridian.commands.resolve import (
    ResolvedServer,
    detect_public_ip,
    ensure_server_connection,
    fetch_credentials,
    is_local_keyword,
    resolve_server,
)
from meridian.config import (
    DEFAULT_PANEL_PORT,
    DEFAULT_SNI,
    SERVERS_FILE,
    creds_dir_for,
    is_ip,
)
from meridian.console import choose, confirm, err_console, fail, info, line, ok, prompt, warn
from meridian.credentials import ServerCredentials
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection


def _remote_meridian_state_exists(resolved: ResolvedServer) -> bool | None:
    """Check whether the server already has Meridian credentials.

    Returns True when `/etc/meridian/proxy.yml` exists and is non-empty, False
    when it is absent, and None when the check itself is inconclusive.
    """
    check = resolved.conn.run("test -s /etc/meridian/proxy.yml", timeout=10)
    if check.returncode == 0:
        return True
    if check.returncode == 1:
        return False
    return None


def _refresh_credentials_before_deploy(resolved: ResolvedServer) -> None:
    """Refresh credentials before deploy.

    Fresh deploys legitimately have nothing to fetch yet. Redeploys try to
    refresh from the server but gracefully fall back to the local cache when
    the server has no credentials (older deploys that pre-date server-side
    credential sync).
    """
    proxy_file = resolved.creds_dir / "proxy.yml"
    had_local_cache = proxy_file.exists()
    if fetch_credentials(resolved, force=True):
        return
    if had_local_cache:
        # Local cache exists but SCP refresh failed. Check why.
        remote_state = _remote_meridian_state_exists(resolved)
        if remote_state is False:
            # Server has no credentials (pre-sync deploy). Local cache is the
            # source of truth — proceed and sync will create the server copy.
            warn("Server has no cached credentials — using local copy (will sync after deploy)")
            return
        if remote_state is True:
            fail(
                "Server credentials exist but could not be fetched",
                hint=(
                    "SCP failed even though the server has /etc/meridian/proxy.yml. "
                    "Run: meridian doctor --user {user}\n"
                    "Check SSH key permissions and scp access.".format(user=resolved.user)
                ),
                hint_type="system",
            )
        # Inconclusive — SSH works (we checked ports) but can't determine
        # remote state. Warn and proceed with the local cache.
        warn("Could not verify server credentials — proceeding with local cache")
        return
    # No local cache — check if server has state from a different machine
    remote_state = _remote_meridian_state_exists(resolved)
    if remote_state is False:
        return  # Fresh deploy, nothing anywhere
    if remote_state is True:
        fail(
            "Server already has Meridian state, but credentials could not be fetched",
            hint=(
                "This server was deployed from another machine. Fetch credentials first:\n"
                "  scp {user}@{ip}:/etc/meridian/proxy.yml {creds_dir}/proxy.yml\n"
                "Then re-run deploy.".format(user=resolved.user, ip=resolved.ip, creds_dir=resolved.creds_dir)
            ),
            hint_type="system",
        )
    # No local cache AND can't determine remote state — allow fresh deploy
    return


def _sync_credentials_to_server(resolved: ResolvedServer) -> bool:
    """Push local proxy.yml to the server's /etc/meridian/.

    Server-side credentials are needed for cron scripts (update-stats.py) and
    for fail-closed refresh in client mutation commands.

    Uses conn.write_file() which handles sudo for non-root users — SCP alone
    can't write to root-owned /etc/meridian/.
    """
    if getattr(resolved, "local_mode", False):
        return True

    proxy_file = resolved.creds_dir / "proxy.yml"
    if not proxy_file.exists():
        return False

    return resolved.conn.write_file(proxy_file, "/etc/meridian/proxy.yml")


def run(
    ip: str = "",
    domain: str = "",
    sni: str = "",
    client_name: str = "",
    user: str = "root",
    yes: bool = False,
    harden: bool = True,
    requested_server: str = "",
    *,
    server_name: str = "",
    icon: str = "",
    color: str = "",
    decoy: str = "",
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    ssh_port: int = 22,
) -> None:
    """Deploy a VLESS+Reality proxy server."""
    # --decoy is deprecated (403/404 is now always the default).
    # Accept silently for backwards compatibility but don't use it.

    registry = ServerRegistry(SERVERS_FILE)
    server_ip = ip
    ssh_user = user

    # --server flag: resolve from registry (or 'local' keyword)
    if requested_server:
        if server_ip:
            fail(
                "Use either the IP address or --server, not both.\n"
                "  Example: meridian deploy 1.2.3.4  OR  meridian deploy --server mybox",
                hint_type="user",
            )
        if is_local_keyword(requested_server):
            server_ip = requested_server
        else:
            entry = registry.find(requested_server)
            if not entry:
                if is_ip(requested_server):
                    server_ip = requested_server
                else:
                    fail(
                        f"Server '{requested_server}' not found",
                        hint="See registered servers: meridian server list",
                        hint_type="user",
                    )
            else:
                server_ip = entry.host
                if user == "root" and entry.user:
                    ssh_user = entry.user

    # Interactive wizard if no IP given
    if not server_ip:
        wizard_result = _interactive_wizard(
            sni=sni,
            domain=domain,
            harden=harden,
            yes=yes,
            client_name=client_name,
            server_name=server_name,
            icon=icon,
            color=color,
            pq=pq,
            warp=warp,
            geo_block=geo_block,
        )
        server_ip, ssh_user, sni, domain, harden = wizard_result[:5]
        client_name, server_name, icon, color, pq, warp, geo_block = wizard_result[5:]

    # Validate IP (skip for 'local' keyword — resolve_server handles it)
    if not is_local_keyword(server_ip) and not is_ip(server_ip):
        fail(
            f"Invalid IP address: {server_ip}",
            hint="Enter a valid IP address (e.g. meridian deploy 123.45.67.89)",
            hint_type="user",
        )

    # Resolve and prepare
    resolved = resolve_server(
        registry,
        explicit_ip=server_ip,
        user=ssh_user,
        port=ssh_port,
    )

    resolved = ensure_server_connection(resolved)
    _check_ports(resolved.conn, resolved.ip, yes)
    _refresh_credentials_before_deploy(resolved)

    # Migrate v1 credentials to v2
    proxy_file = resolved.creds_dir / "proxy.yml"
    if proxy_file.exists():
        creds = ServerCredentials.load(proxy_file)
        if creds.has_credentials:
            creds.save(proxy_file)  # Re-save as v2 if loaded from v1

    # Without --sni, a redeploy keeps the server's camouflage target (changing it
    # breaks every client link issued with the old SNI); otherwise suggest a scan result.
    if not sni:
        proxy_file = resolved.creds_dir / "proxy.yml"
        if proxy_file.exists():
            creds = ServerCredentials.load(proxy_file)
            if creds.server.sni:
                info(f"Keeping the current camouflage target: {creds.server.sni} (pass --sni to change it)")
                sni = creds.server.sni
            elif creds.server.scanned_sni:
                info(f"Detected optimal SNI from scan: {creds.server.scanned_sni}")
                if yes:
                    sni = creds.server.scanned_sni
                else:
                    choice = choose(
                        "SNI target",
                        [
                            f"Use {creds.server.scanned_sni}",
                            f"Skip \u2014 use default ({DEFAULT_SNI})",
                        ],
                    )
                    if choice == 1:
                        sni = creds.server.scanned_sni

    # Route to legacy Ansible or new Python provisioner
    if client_name and not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", client_name):
        fail(
            f"Client name '{client_name}' is invalid",
            hint="Use letters, numbers, hyphens, and underscores.",
            hint_type="user",
        )

    # Save branding to credentials before provisioning
    if server_name or icon or color:
        from meridian.credentials import BrandingConfig

        proxy_file = resolved.creds_dir / "proxy.yml"
        creds = ServerCredentials.load(proxy_file) if proxy_file.exists() else ServerCredentials()
        creds.branding = BrandingConfig(
            server_name=server_name,
            icon=icon,
            color=color,
        )
        creds.save(proxy_file)

    _run_provisioner(resolved, domain, sni, client_name, harden, pq=pq, warp=warp, geo_block=geo_block)
    if not _sync_credentials_to_server(resolved):
        warn("Could not sync credentials to server — client commands may need a redeploy")
    try:
        _regenerate_connection_pages_after_deploy(resolved)
    except Exception as exc:
        warn(f"Could not refresh connection pages after deploy: {exc}")

    # Register server
    registry.add(ServerEntry(host=resolved.ip, user=resolved.user, port=getattr(resolved.conn, "port", 22)))

    # Success output
    redeploy_cmd = _build_redeploy_command(
        resolved,
        sni=sni,
        domain=domain,
        client_name=client_name,
        harden=harden,
        server_name=server_name,
        icon=icon,
        color=color,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
    )
    _print_success(resolved, client_name, domain, redeploy_cmd=redeploy_cmd)

    # Offer relay setup
    _offer_relay(resolved, yes)


def _interactive_wizard(
    sni: str,
    domain: str,
    harden: bool,
    yes: bool,
    client_name: str = "",
    server_name: str = "",
    icon: str = "",
    color: str = "",
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
) -> tuple[str, str, str, str, bool, str, str, str, str, bool, bool, bool]:
    """Interactive deployment wizard.

    Returns (ip, user, sni, domain, harden,
             client_name, server_name, icon, color, pq, warp, geo_block).
    """
    import os

    # --- Protocol explanation ---
    err_console.print()
    info("Protocol: VLESS + Reality")
    err_console.print("  [dim]Your server impersonates a real website \u2014 censors see[/dim]")
    err_console.print("  [dim]normal HTTPS traffic, not a VPN connection.[/dim]")
    err_console.print()

    # --- Server IP ---
    detected_ip = detect_public_ip()
    is_local = False

    # Offer local deployment if running as root with a public IP
    if detected_ip and os.getuid() == 0:
        info(f"Detected: running as root on this server ({detected_ip})")
        err_console.print()
        choice = choose(
            "Deploy target",
            [
                f"This server ({detected_ip}) \u2014 local mode",
                "Another server \u2014 enter IP",
            ],
        )
        if choice == 1:
            server_ip = "local"
            ssh_user = "root"
            is_local = True
        else:
            is_local = False

    if not is_local:
        while True:
            server_ip = prompt("Server IP address", default=detected_ip)
            if is_ip(server_ip) or is_local_keyword(server_ip):
                break
            err_console.print("  [error]Enter a valid IP address (e.g. 123.45.67.89)[/error]")

        if is_local_keyword(server_ip):
            is_local = True
            ssh_user = "root"
        else:
            # --- SSH user ---
            import re

            while True:
                ssh_user = prompt("SSH user", default="root")
                if re.match(r"^[a-zA-Z0-9._-]+$", ssh_user):
                    break
                err_console.print("  [error]Use letters, numbers, dots, hyphens, and underscores only[/error]")
            if ssh_user != "root":
                err_console.print("  [dim](sudo will be used for privileged operations)[/dim]")

    # --- Server hardening ---
    if not yes:
        err_console.print()
        err_console.print("  [bold]Server hardening[/bold]")
        err_console.print("  [dim]Disables password SSH login and enables firewall[/dim]")
        err_console.print("  [dim](allows ports 22, 80, 443 only). Skip if you have[/dim]")
        err_console.print("  [dim]other services running on this server.[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "Yes \u2014 harden SSH and firewall [dim](recommended)[/dim]",
                "No \u2014 keep current settings",
            ],
        )
        if choice == 2:
            harden = False
            warn("Skipping SSH hardening and firewall")
        else:
            harden = True

    # --- Offer scan for SNI ---
    if not sni:
        err_console.print()
        err_console.print("  [bold]Camouflage target[/bold]")
        err_console.print("  [dim]Pick any popular website (you don't need to own it).[/dim]")
        err_console.print("  [dim]Your server will impersonate it — censors probing see[/dim]")
        err_console.print("  [dim]that real site's certificate. Scanning finds targets on[/dim]")
        err_console.print("  [dim]the same network, which are hardest to distinguish.[/dim]")
        err_console.print()

        # Check for the server's current and previously scanned SNI
        saved_sni = ""
        saved_scanned_sni = ""
        creds_dir = creds_dir_for(server_ip, local_mode=is_local)
        if (creds_dir / "proxy.yml").exists():
            saved_creds = ServerCredentials.load(creds_dir / "proxy.yml")
            saved_sni = saved_creds.server.sni or ""
            saved_scanned_sni = saved_creds.server.scanned_sni or ""

        # A redeploy keeps the server's camouflage target: changing it breaks every
        # client link issued with the old SNI. Pass --sni to change it on purpose.
        if saved_sni:
            info(f"Keeping the current camouflage target: {saved_sni} (pass --sni to change it)")
            sni = saved_sni
        elif saved_scanned_sni:
            info(f"Previous scan found: {saved_scanned_sni}")
            if not yes:
                choice = choose(
                    "Camouflage target",
                    [
                        f"Use {saved_scanned_sni}",
                        "Scan again",
                        f"Skip \u2014 use default ({DEFAULT_SNI})",
                    ],
                )
                if choice == 1:
                    sni = saved_scanned_sni
                elif choice == 3:
                    sni = DEFAULT_SNI
            else:
                sni = saved_scanned_sni

        if not sni and not yes:
            choice = choose(
                "Camouflage",
                [
                    "Scan for optimal target (~1 minute)",
                    "Enter manually",
                    f"Skip \u2014 use default ({DEFAULT_SNI})",
                ],
            )
            if choice == 2:
                sni = prompt("SNI domain (e.g. example.com)")
            elif choice == 1:
                # Establish connection for scan
                try:
                    scan_ip = detected_ip if is_local else server_ip
                    conn = ServerConnection(ip=scan_ip, user=ssh_user, local_mode=is_local)
                    if not is_local:
                        conn.detect_local_mode()
                        if not conn.local_mode:
                            conn.check_ssh()

                    from meridian.commands.scan import scan_for_sni

                    candidates = scan_for_sni(conn, scan_ip)

                    if candidates:
                        top = candidates[:5]
                        options = list(top) + [f"[dim]Skip \u2014 use default ({DEFAULT_SNI})[/dim]"]
                        err_console.print()
                        pick = choose("Choose", options)
                        if pick <= len(top):
                            sni = top[pick - 1]

                            # Save scanned SNI to credentials
                            creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                            proxy_file = creds_dir / "proxy.yml"
                            creds = ServerCredentials.load(proxy_file)
                            creds.server.scanned_sni = sni
                            creds.save(proxy_file)
                    else:
                        warn("No targets found on the same network")
                except Exception:
                    warn("Could not connect to scan. You can run 'meridian scan' later.")

        if not sni:
            sni = DEFAULT_SNI

    # --- Domain (optional but strongly recommended) ---
    err_console.print()
    err_console.print("  [bold]Domain[/bold] [dim](strongly recommended)[/dim]")
    err_console.print("  [dim]Makes your server indistinguishable from a normal website.[/dim]")
    err_console.print("  [dim]Without a domain, probers see an IP-only certificate —[/dim]")
    err_console.print("  [dim]a valid but less common profile. Also enables CDN fallback.[/dim]")
    err_console.print("  [dim]Guide: getmeridian.org/docs/en/domain-mode/[/dim]")

    # Suggest domain from saved credentials
    suggested_domain = domain
    if not suggested_domain:
        domain_creds_dir = creds_dir_for(server_ip, local_mode=is_local)
        if (domain_creds_dir / "proxy.yml").exists():
            saved_creds = ServerCredentials.load(domain_creds_dir / "proxy.yml")
            suggested_domain = saved_creds.server.domain or ""

    if suggested_domain:
        err_console.print(f"  [dim]Detected: {suggested_domain}[/dim]")

    if not yes:
        domain_input = prompt("Domain (leave blank to skip)", default=suggested_domain or "")
        if domain_input and domain_input != "skip":
            domain = domain_input
        elif not domain_input or domain_input == "skip":
            domain = ""
    elif not domain:
        domain = ""

    # --- Branding: server name ---
    if not yes and not server_name:
        err_console.print()
        err_console.print("  [bold]Personalize[/bold]")
        err_console.print("  [dim]Make the connection page yours. Your friends see this.[/dim]")
        err_console.print()
        server_name = prompt("Server name", default="My VPN")

    # --- Branding: icon ---
    if not yes and not icon:
        from meridian.branding import ICON_SUGGESTIONS

        err_console.print()
        err_console.print("  [bold]Server icon[/bold]")
        grid = "    "
        for i, emoji in enumerate(ICON_SUGGESTIONS, 1):
            grid += f"{i}. {emoji}  "
        err_console.print(grid)
        err_console.print()
        icon_input = prompt("Pick a number, paste an emoji, or an image URL", default="1")
        if icon_input.isdigit():
            idx = int(icon_input) - 1
            if 0 <= idx < len(ICON_SUGGESTIONS):
                icon = ICON_SUGGESTIONS[idx]
        if not icon:
            from meridian.branding import process_icon

            icon = process_icon(icon_input)
    elif icon:
        # CLI flag provided — process it
        from meridian.branding import process_icon

        processed = process_icon(icon)
        if processed:
            icon = processed

    # --- Branding: color palette ---
    if not yes and not color:
        from meridian.branding import PALETTE_LABELS, PALETTES

        err_console.print()
        err_console.print("  [bold]Color palette[/bold]")
        palette_names = list(PALETTES.keys())
        options = []
        for pname in palette_names:
            marker = " [dim](default)[/dim]" if pname == "ocean" else ""
            options.append(f"{PALETTE_LABELS[pname]}{marker}")
        err_console.print()
        color_pick = choose("Choose", options)
        idx = color_pick - 1
        if 0 <= idx < len(palette_names):
            color = palette_names[idx]
    elif color:
        from meridian.branding import validate_color

        color = validate_color(color) or "ocean"

    if not color:
        color = "ocean"

    # --- Client name ---
    if not yes and not client_name:
        err_console.print()
        err_console.print("  [bold]First client[/bold]")
        err_console.print("  [dim]Name for the first connection profile (you can add more later).[/dim]")
        err_console.print()
        client_name = prompt("Client name", default="default")

    if not client_name:
        client_name = "default"

    # --- Post-quantum encryption ---
    if not yes and not pq:
        err_console.print()
        err_console.print("  [bold]Post-quantum encryption[/bold] [dim](experimental)[/dim]")
        err_console.print("  [dim]Adds ML-KEM-768 hybrid encryption on top of Reality.[/dim]")
        err_console.print("  [dim]Only tested with Happ and v2RayTun. Some apps may not connect.[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "No \u2014 standard encryption [dim](all apps)[/dim]",
                "Yes \u2014 post-quantum [dim](tested: Happ, v2RayTun)[/dim]",
            ],
        )
        if choice == 2:
            pq = True

    # --- Cloudflare WARP ---
    if not yes and not warp:
        err_console.print()
        err_console.print("  [bold]Cloudflare WARP[/bold] [dim](optional)[/dim]")
        err_console.print("  [dim]Routes outgoing traffic through Cloudflare so websites[/dim]")
        err_console.print("  [dim]see a Cloudflare IP, not your server's real IP.[/dim]")
        err_console.print()
        err_console.print("  [dim]Useful when:[/dim]")
        err_console.print("  [dim]  • Websites block datacenter/VPS IP ranges[/dim]")
        err_console.print("  [dim]  • You want to hide the VPS IP from destination sites[/dim]")
        err_console.print()
        err_console.print("  [dim]Not needed when:[/dim]")
        err_console.print("  [dim]  • Normal browsing already works fine through the proxy[/dim]")
        err_console.print("  [dim]  • You want maximum speed (WARP adds an extra hop)[/dim]")
        err_console.print()
        err_console.print("  [dim]Technical: installs cloudflare-warp on the server in SOCKS5[/dim]")
        err_console.print("  [dim]proxy mode. Only outgoing proxy traffic is routed through[/dim]")
        err_console.print("  [dim]WARP — incoming connections (SSH, etc.) are unaffected.[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "No \u2014 direct connection [dim](default, fastest)[/dim]",
                "Yes \u2014 route through Cloudflare WARP",
            ],
        )
        if choice == 2:
            warp = True

    # --- Geo-blocking ---
    if not yes and geo_block:
        err_console.print()
        err_console.print("  [bold]Geo-blocking[/bold]")
        err_console.print("  [dim]Blocks access to Russian websites and IPs through[/dim]")
        err_console.print("  [dim]the proxy (geosite:category-ru + geoip:ru).[/dim]")
        err_console.print()
        err_console.print("  [dim]Why enable:[/dim]")
        err_console.print("  [dim]  • Prevents your VPN server IP from appearing in logs[/dim]")
        err_console.print("  [dim]    of Russian services — reduces risk of it being blocked[/dim]")
        err_console.print("  [dim]  • Russian sites work fine without a VPN anyway[/dim]")
        err_console.print()
        err_console.print("  [dim]Why disable:[/dim]")
        err_console.print("  [dim]  • You need to access .ru sites through the proxy[/dim]")
        err_console.print("  [dim]  • You want all traffic to go through the VPN with no[/dim]")
        err_console.print("  [dim]    exceptions[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "Yes \u2014 block Russian traffic [dim](recommended, protects server IP)[/dim]",
                "No \u2014 allow all traffic [dim](Russian sites accessible through proxy)[/dim]",
            ],
        )
        if choice == 2:
            geo_block = False

    # --- Summary panel ---
    from rich.panel import Panel

    protocol_line = "VLESS + Reality (TCP)\n           + XHTTP fallback (same port)"
    if domain:
        protocol_line += f"\n           + CDN fallback ({domain})"

    encryption_line = ""
    if pq:
        encryption_line = "\nEncryption: Post-quantum (ML-KEM-768 hybrid) [dim]experimental[/dim]"

    warp_line = ""
    if warp:
        warp_line = "\nWARP:       Outgoing traffic via Cloudflare"

    geo_block_line = (
        "\nGeo-block:  Enabled (.ru / Russian IP traffic blocked)"
        if geo_block
        else "\nGeo-block:  Disabled (Russian sites accessible)"
    )

    icon_display = icon if icon and not icon.startswith("data:") else ""
    branding_line = ""
    if server_name or icon_display or color:
        parts = []
        if icon_display:
            parts.append(icon_display)
        if server_name:
            parts.append(server_name)
        if color:
            parts.append(f"[dim]{color} palette[/dim]")
        branding_line = f"\nBranding:   {' '.join(parts)}"

    server_label = f"this server ({detected_ip}) \u2014 local mode" if is_local else f"{ssh_user}@{server_ip}"
    harden_label = "SSH hardened + firewall" if harden else "skipped"
    summary = (
        f"Server:     {server_label}\n"
        f"Protocol:   {protocol_line}\n"
        f"Camouflage: {sni}\n"
        f"Hardening:  {harden_label}\n"
        f"Client:     {client_name}\n"
        f"Mode:       {'Domain mode (best stealth + CDN fallback)' if domain else 'IP-only (works without a domain)'}"
        f"{encryption_line}"
        f"{warp_line}"
        f"{geo_block_line}"
        f"{branding_line}"
    )

    err_console.print()
    err_console.print(Panel(summary, title="[bold]Deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()

    # --- Confirm ---
    if not yes:
        if is_local:
            confirm(f"Deploy locally on this server ({detected_ip})?")
        else:
            confirm(f"Deploy to {ssh_user}@{server_ip}?")
    err_console.print()

    return server_ip, ssh_user, sni, domain, harden, client_name, server_name, icon, color, pq, warp, geo_block


def _run_provisioner(
    resolved: ResolvedServer,
    domain: str,
    sni: str,
    client_name: str,
    harden: bool = True,
    *,
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
) -> None:
    """Run the Python provisioner pipeline."""
    from meridian.provision import ProvisionContext, Provisioner, build_setup_steps

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        domain=domain,
        sni=sni or DEFAULT_SNI,
        xhttp_enabled=True,
        pq_encryption=pq,
        warp=warp,
        geo_block=geo_block,
        hosted_page=True,  # always serve connection pages on server
        harden=harden,
        creds_dir=str(resolved.creds_dir),
    )

    # Default panel port — 3x-ui starts on 2053. ConfigurePanel may change it later.
    ctx.panel_port = DEFAULT_PANEL_PORT
    # Deterministic port derivation (hashlib, not hash() which is randomized per process)
    ip_hash = int(hashlib.sha256(ctx.ip.encode()).hexdigest()[:8], 16)
    ctx.xhttp_port = 30000 + (ip_hash % 10000)
    ctx.reality_port = 443 if not ctx.needs_web_server else (10000 + ip_hash % 1000)
    ctx.wss_port = 20000 + (ip_hash % 10000)

    # Load existing credentials into context if available
    proxy_file = Path(ctx.creds_dir) / "proxy.yml"
    if proxy_file.exists():
        from meridian.credentials import ServerCredentials

        creds = ServerCredentials.load(proxy_file)
        ctx["credentials"] = creds
        if creds.panel.username and creds.panel.password:
            ctx["panel_configured"] = True
            ctx["panel_username"] = creds.panel.username
            ctx["panel_password"] = creds.panel.password
            ctx["web_base_path"] = creds.panel.web_base_path or ""
            ctx["info_page_path"] = creds.panel.info_page_path or ""
            # Use saved panel port (not computed) for re-runs
            if creds.panel.port:
                ctx.panel_port = creds.panel.port
            # Load protocol paths so downstream steps (InstallNginx) have them
            # even when ConfigurePanel is skipped on re-deploy
            if creds.wss.ws_path:
                ctx["ws_path"] = creds.wss.ws_path
            if creds.xhttp.xhttp_path:
                ctx["xhttp_path"] = creds.xhttp.xhttp_path

    # First client name
    ctx["first_client_name"] = client_name or "default"

    err_console.print()
    info(f"Configuring server at {ctx.ip}...")
    if domain:
        info(f"Domain: {domain}")
    if sni and sni != DEFAULT_SNI:
        info(f"SNI: {sni}")
    if pq:
        info("Post-quantum encryption: enabled (experimental)")
    if warp:
        info("Cloudflare WARP: enabled")
    if not geo_block:
        info("Geo-blocking: disabled (Russian sites accessible)")
    err_console.print()

    steps = build_setup_steps(ctx)
    provisioner = Provisioner(steps)

    conn = resolved.conn
    if not isinstance(conn, ServerConnection):
        fail("No SSH connection available", hint_type="bug")

    results = provisioner.run(conn, ctx)

    # Check for failures
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail(
            "Setup failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}\nRun: meridian preflight {ctx.ip}",
            hint_type="system",
        )

    err_console.print()
    ok("All steps completed successfully")


def _build_redeploy_command(
    resolved: ResolvedServer,
    *,
    sni: str,
    domain: str,
    client_name: str,
    harden: bool,
    server_name: str,
    icon: str,
    color: str,
    pq: bool,
    warp: bool,
    geo_block: bool,
) -> str:
    """Build a non-interactive meridian deploy command that reproduces the current config."""
    parts = ["meridian deploy", shlex.quote(resolved.ip)]

    if resolved.user != "root":
        parts.append(f"--user {shlex.quote(resolved.user)}")
    if sni and sni != DEFAULT_SNI:
        parts.append(f"--sni {shlex.quote(sni)}")
    if domain:
        parts.append(f"--domain {shlex.quote(domain)}")
    if client_name and client_name != "default":
        parts.append(f"--client-name {shlex.quote(client_name)}")
    if not harden:
        parts.append("--no-harden")
    if pq:
        parts.append("--pq")
    if warp:
        parts.append("--warp")
    if not geo_block:
        parts.append("--no-geo-block")
    if server_name:
        parts.append(f"--display-name {shlex.quote(server_name)}")
    if icon:
        parts.append(f"--icon {shlex.quote(icon)}")
    if color and color != "ocean":
        parts.append(f"--color {shlex.quote(color)}")
    parts.append("--yes")

    return " ".join(parts)


def _print_success(
    resolved: ResolvedServer,
    client_name: str,
    domain: str,
    *,
    redeploy_cmd: str,
) -> None:
    """Print success output after deployment."""
    client_label = client_name or "default"
    creds_dir = resolved.creds_dir
    html_files = list(creds_dir.glob(f"*-{client_label}-connection-info.html"))

    # Check if there's a hosted page URL from credentials
    hosted_page_url = ""
    proxy_file = creds_dir / "proxy.yml"
    if proxy_file.exists():
        creds = ServerCredentials.load(proxy_file)
        if creds.server.hosted_page and creds.panel.info_page_path and creds.reality.uuid:
            host = creds.server.domain or creds.server.ip or resolved.ip
            hosted_page_url = f"https://{host}/{creds.panel.info_page_path}/{creds.reality.uuid}/"

    err_console.print("\n  [ok][bold]Done![/bold][/ok]\n")
    ok("Your proxy server is live and ready to use.")
    err_console.print()
    err_console.print("  [bold]Next steps:[/bold]\n")

    if hosted_page_url:
        err_console.print("  [ok]1.[/ok] Share this link with whoever needs access:")
        err_console.print(f"     [bold]{hosted_page_url}[/bold]")
        err_console.print("     [dim](They open it, scan the QR code, and connect)[/dim]\n")
    elif html_files:
        err_console.print("  [ok]1.[/ok] Send this file to whoever needs access:")
        err_console.print(f"     [bold]{html_files[0]}[/bold]")
        err_console.print("     [dim](They open it, scan the QR code, and connect)[/dim]\n")

    err_console.print("  [ok]2.[/ok] View connection details anytime:")
    err_console.print(f"     [info]meridian client show {client_label}[/info]\n")

    err_console.print("  [ok]3.[/ok] Test that the proxy works:")
    server_ip = resolved.ip
    err_console.print(f"     [info]meridian test {server_ip}[/info]")
    err_console.print("     [dim]Run it after deploy/redeploy to verify the live server state.[/dim]\n")

    if proxy_file.exists():
        if creds.server.geo_block:
            err_console.print(
                "  [dim]Geo-blocking is ON: .ru domains and Russian IPs are blocked through the proxy.[/dim]"
            )
            err_console.print("  [dim]Re-deploy with --no-geo-block if you need those sites through Meridian.[/dim]\n")
        else:
            err_console.print("  [dim]Geo-blocking is OFF: all destinations are reachable through the proxy.[/dim]\n")

    next_step = 4
    if domain:
        err_console.print("  [ok]4.[/ok] Cloudflare setup:")
        err_console.print(f"     [dim]A record {domain} -> {server_ip}[/dim]")
        err_console.print("     [dim]Keep it DNS only (grey cloud) during deploy/redeploy[/dim]")
        err_console.print("     [dim]After deploy succeeds: switch to Proxied (orange cloud)[/dim]")
        err_console.print("     [dim]Set SSL/TLS to Full (Strict) and enable WebSockets[/dim]\n")
        err_console.print("     [dim]Disable features that inject scripts or rewrite HTML on this hostname[/dim]")
        err_console.print("     [dim](for example Website Analytics / RUM), or the connection page can break[/dim]\n")
        next_step = 5

    err_console.print(f"  [ok]{next_step}.[/ok] Share access with friends:")
    err_console.print("     [info]meridian client add alice[/info]")
    err_console.print("     [info]meridian client list[/info]\n")

    server_ip = resolved.ip
    err_console.print(f"  [ok]{next_step + 1}.[/ok] Add a relay for resilience (optional):")
    err_console.print(f"     [info]meridian relay deploy RELAY_IP --exit {server_ip}[/info]")
    err_console.print("     [dim]Routes through a domestic IP when the exit gets blocked[/dim]\n")

    err_console.print()
    line()

    # Redeploy command
    err_console.print("\n  [dim]Re-deploy with the same settings:[/dim]")
    err_console.print(f"  [dim]  {redeploy_cmd}[/dim]")

    # Panel access for advanced users
    if proxy_file.exists() and creds.panel.url and creds.panel.username:
        err_console.print("\n  [dim]3x-ui panel (advanced — monitor traffic, manage inbounds):[/dim]")
        err_console.print(f"  [dim]  {creds.panel.url}[/dim]")
        err_console.print(f"  [dim]  {creds.panel.username} / {creds.panel.password}[/dim]")

    err_console.print("\n  [dim]Feedback & issues: https://github.com/uburuntu/meridian/issues[/dim]\n")


def _regenerate_connection_pages_after_deploy(resolved: ResolvedServer) -> None:
    """Refresh local and hosted client handoff pages after deploy/redeploy."""
    proxy_file = resolved.creds_dir / "proxy.yml"
    if not proxy_file.exists():
        return

    creds = ServerCredentials.load(proxy_file)
    if not creds.clients:
        return

    from meridian.commands.relay import _regenerate_client_pages

    _regenerate_client_pages(resolved, creds)


def _check_ports(conn: ServerConnection, ip: str, yes: bool) -> None:
    """Check that ports 443 and 80 are available before deploying.

    Allows re-deploy over existing Meridian processes.
    Loops with retry prompt if a non-Meridian process holds a port.
    """
    allowed = {"nginx", "3x-ui", "xray", "haproxy", "caddy"}

    for port in (443, 80):
        while True:
            result = conn.run(f"ss -tlnp sport = :{port} 2>/dev/null | grep LISTEN", timeout=10)
            if not result.stdout.strip():
                break  # port free

            match = re.search(r'users:\(\("([^"]*)"', result.stdout)
            proc = match.group(1) if match else "unknown"
            if proc in allowed:
                break  # Meridian's own process — OK for re-deploy

            warn(f"Port {port} is in use by {proc}")
            err_console.print(f"  [dim]Port {port} must be free for Meridian.[/dim]")
            err_console.print(f"  [dim]Stop {proc} and retry, or press Ctrl+C to abort.[/dim]")
            err_console.print()

            if yes:
                fail(
                    f"Port {port} is occupied by {proc}",
                    hint=f"Stop {proc} first: sudo systemctl stop {proc}",
                    hint_type="user",
                )

            choice = choose("Retry?", ["Yes", "No"])
            if choice == 2:
                fail("Aborted — port conflict", hint_type="user")


def _offer_relay(resolved: ResolvedServer, yes: bool) -> None:
    """Offer to deploy a relay node after successful exit server deploy."""
    if yes:
        return  # Don't prompt in non-interactive mode

    err_console.print()
    err_console.print("  [bold]Add a relay node?[/bold] [dim](optional)[/dim]")
    err_console.print("  [dim]A relay is a domestic server that forwards traffic to[/dim]")
    err_console.print("  [dim]this exit server. Useful when the IP gets blocked.[/dim]")
    err_console.print()

    choice = choose(
        "Set up a relay?",
        [
            "No \u2014 skip for now",
            "Yes \u2014 add a relay node",
        ],
    )
    if choice == 1:
        err_console.print(f"  [dim]You can add one later: meridian relay deploy RELAY_IP --exit {resolved.ip}[/dim]")
        return

    relay_ip = prompt("Relay server IP")
    if not is_ip(relay_ip):
        warn(f"Invalid IP. Set up later: meridian relay deploy RELAY_IP --exit {resolved.ip}")
        return

    relay_name = prompt("Relay name (optional, e.g. ru-moscow)", default="")

    from meridian.commands.relay import run_deploy

    run_deploy(
        relay_ip=relay_ip,
        exit_arg=resolved.ip,
        user="root",
        relay_name=relay_name,
        listen_port=443,
        yes=False,
    )
