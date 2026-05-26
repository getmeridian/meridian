"""Relay management — deploy, list, remove, check relay nodes."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

import yaml

from meridian.commands.resolve import (
    ResolvedServer,
    ensure_server_connection,
    fetch_credentials,
    resolve_server,
)
from meridian.config import (
    CREDS_BASE,
    DEFAULT_SNI,
    RELAY_SERVICE_NAME,
    SERVER_CREDS_DIR,
    SERVERS_FILE,
    is_ip,
    sanitize_ip_for_path,
)
from meridian.console import confirm, err_console, fail, info, line, ok, warn
from meridian.credentials import RelayEntry, ServerCredentials
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError

# ---------------------------------------------------------------------------
# Relay inbound helpers
# ---------------------------------------------------------------------------


def _relay_label(relay: RelayEntry) -> str:
    """Derive a filesystem/remark-safe label from a relay entry."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", relay.name or relay.ip)


def _relay_inbound_remark(relay: RelayEntry) -> str:
    """3x-ui inbound remark for a relay-specific Reality inbound."""
    return f"VLESS-Reality-Relay-{_relay_label(relay)}"


def _relay_xray_port(relay_ip: str) -> int:
    """Deterministic Xray port for a relay inbound (range 40000-49999)."""
    ip_hash = int(hashlib.sha256(relay_ip.encode()).hexdigest()[:8], 16)
    return 40000 + (ip_hash % 10000)


def _create_relay_inbound(
    exit_conn: ServerConnection,
    creds: ServerCredentials,
    relay_sni: str,
    relay_ip: str,
    relay_name: str = "",
) -> bool:
    """Create a relay-specific Xray Reality inbound on the exit server.

    Uses the same key pair and client list as the main Reality inbound,
    but with the relay's SNI as dest (so probes see the correct cert).

    Returns True on success, False on failure.
    """
    from meridian.panel import PanelClient, PanelError
    from meridian.provision.xray import SNIFFING_JSON, _reality_stream_settings

    panel = PanelClient(
        conn=exit_conn,
        panel_port=creds.panel.port,
        web_base_path=creds.panel.web_base_path or "",
    )
    try:
        panel.login(creds.panel.username or "", creds.panel.password or "")
    except PanelError as e:
        warn(f"Could not connect to exit panel: {e}")
        return False

    with panel:
        port = _relay_xray_port(relay_ip)
        temp_entry = RelayEntry(ip=relay_ip, name=relay_name)
        remark = _relay_inbound_remark(temp_entry)

        # Check if already exists
        existing = panel.find_inbound(remark)
        if existing is not None:
            ok(f"Relay inbound already exists: {remark}")
            return True

        # Build stream settings with relay SNI
        stream_settings = _reality_stream_settings(
            sni=relay_sni,
            private_key=creds.reality.private_key or "",
            public_key=creds.reality.public_key or "",
            short_id=creds.reality.short_id or "",
        )

        # Copy client list from main Reality inbound
        from meridian.protocols import get_protocol

        reality_proto = get_protocol("reality")
        if reality_proto is None:
            warn("Reality protocol not registered")
            return False

        try:
            inbounds = panel.list_inbounds()
        except PanelError as e:
            warn(f"Could not list inbounds: {e}")
            return False

        main_ib = reality_proto.find_inbound(inbounds)
        if main_ib is None:
            warn("Main Reality inbound not found — cannot create relay inbound")
            return False

        # Build settings with same clients as main inbound
        clients_list = []
        for client in main_ib.clients:
            clients_list.append(
                {
                    "id": client.get("id", ""),
                    "flow": "xtls-rprx-vision",
                    "email": f"relay-{_relay_label(temp_entry)}-{client.get('email', '').split('-', 1)[-1]}",
                    "limitIp": client.get("limitIp", 2),
                    "totalGB": 0,
                    "expiryTime": 0,
                    "enable": True,
                    "tgId": "",
                    "subId": "",
                    "reset": 0,
                }
            )

        if not clients_list:
            # No clients yet — create with a placeholder that will be updated
            # when the first client is added
            warn("No clients in main inbound — relay inbound will have no clients until next client add")

        settings: dict = {
            "clients": clients_list,
            "decryption": "none",
            "fallbacks": [],
        }
        # PQ encryption: match main inbound
        if creds.reality.encryption_private_key:
            settings["decryption"] = creds.reality.encryption_private_key
            del settings["fallbacks"]

        body = {
            "remark": remark,
            "enable": True,
            "listen": "127.0.0.1",  # Behind nginx
            "port": port,
            "protocol": "vless",
            "expiryTime": 0,
            "total": 0,
            "settings": json.dumps(settings),
            "streamSettings": stream_settings,
            "sniffing": SNIFFING_JSON,
        }

        try:
            data = panel.api_post_json("/panel/api/inbounds/add", body)
        except PanelError as e:
            warn(f"Failed to create relay inbound: {e}")
            return False

        if not data.get("success"):
            warn(f"Failed to create relay inbound: {data.get('msg', 'unknown error')}")
            return False

        ok(f"Relay inbound created on exit (port {port}, SNI={relay_sni})")
        return True


def _deploy_relay_nginx(
    exit_conn: ServerConnection,
    relay_sni: str,
    relay_ip: str,
    relay_name: str = "",
) -> bool:
    """Create per-relay nginx config files on the exit server and reload.

    Creates two files:
    1. Map entry in relay-maps/ (routes SNI to relay upstream)
    2. Upstream definition (Xray inbound port for this relay)

    Also ensures the main stream config includes relay-maps (one-time
    migration for servers deployed before per-relay SNI support).
    """
    temp_entry = RelayEntry(ip=relay_ip, name=relay_name)
    label = _relay_label(temp_entry)
    port = _relay_xray_port(relay_ip)
    upstream_name = f"xray_relay_{label}"

    # Ensure main stream config includes relay-maps (migration for pre-existing deploys)
    exit_conn.run(
        "grep -q 'relay-maps' /etc/nginx/stream.d/meridian.conf 2>/dev/null || "
        r"sed -i '/map \$ssl_preread_server_name/a\\    include /etc/nginx/stream.d/relay-maps/*.conf;' "
        "/etc/nginx/stream.d/meridian.conf",
        timeout=15,
    )

    # Map entry: relay_sni -> upstream
    map_content = f"    {relay_sni}  {upstream_name};\n"
    q_map = shlex.quote(map_content)
    exit_conn.run(
        f"mkdir -p /etc/nginx/stream.d/relay-maps && "
        f"printf '%s' {q_map} > /etc/nginx/stream.d/relay-maps/{shlex.quote(label)}.conf",
        timeout=15,
    )

    # Upstream: relay Xray inbound port
    upstream_content = f"upstream {upstream_name} {{\n    server 127.0.0.1:{port};\n}}\n"
    q_upstream = shlex.quote(upstream_content)
    exit_conn.run(
        f"printf '%s' {q_upstream} > /etc/nginx/stream.d/meridian-relay-{shlex.quote(label)}.conf",
        timeout=15,
    )

    # Validate and reload
    result = exit_conn.run("nginx -t 2>&1", timeout=15)
    if result.returncode != 0:
        warn(f"nginx config validation failed: {result.stderr.strip() or result.stdout.strip()}")
        return False
    reload_result = exit_conn.run("systemctl reload nginx", timeout=15)
    if reload_result.returncode != 0:
        warn(f"nginx reload failed: {reload_result.stderr.strip() or reload_result.stdout.strip()}")
        return False
    ok(f"nginx updated: SNI={relay_sni} -> port {port}")
    return True


def _remove_relay_nginx(
    exit_conn: ServerConnection,
    relay: RelayEntry,
) -> bool:
    """Remove per-relay nginx config files from the exit server and reload."""
    label = _relay_label(relay)
    q_label = shlex.quote(label)
    exit_conn.run(
        f"rm -f /etc/nginx/stream.d/relay-maps/{q_label}.conf /etc/nginx/stream.d/meridian-relay-{q_label}.conf",
        timeout=15,
    )
    result = exit_conn.run("nginx -t 2>&1", timeout=15)
    if result.returncode != 0:
        warn(f"nginx config validation failed after relay removal: {result.stderr.strip() or result.stdout.strip()}")
        return False
    reload_result = exit_conn.run("systemctl reload nginx", timeout=15)
    if reload_result.returncode != 0:
        warn(f"nginx reload failed after relay removal: {reload_result.stderr.strip() or reload_result.stdout.strip()}")
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_exit(
    registry: ServerRegistry,
    exit_arg: str,
    user: str,
    *,
    force_refresh: bool = False,
) -> ResolvedServer:
    """Resolve and validate the exit server."""
    if is_ip(exit_arg):
        resolved = resolve_server(registry, explicit_ip=exit_arg, user=user)
    else:
        resolved = resolve_server(registry, requested_server=exit_arg, user=user)

    resolved = ensure_server_connection(resolved)
    if force_refresh and not fetch_credentials(resolved, force=True):
        fail(
            f"Could not refresh credentials from exit server {resolved.ip}",
            hint="Check SSH/SCP connectivity. Relay commands require fresh exit credentials.",
            hint_type="system",
        )
    elif not force_refresh:
        fetch_credentials(resolved, force=False)

    # Verify exit is deployed
    proxy_file = resolved.creds_dir / "proxy.yml"
    if not proxy_file.exists():
        fail(
            "Exit server is not deployed",
            hint=f"Deploy it first: meridian deploy {resolved.ip}",
            hint_type="user",
        )

    creds = ServerCredentials.load(proxy_file)
    if not creds.has_credentials:
        fail(
            "Exit server has no panel credentials",
            hint=f"Deploy it first: meridian deploy {resolved.ip}",
            hint_type="user",
        )

    return resolved


def _find_proxy_file(host: str) -> Path | None:
    """Find proxy.yml for a server, checking both remote and local-mode paths."""
    remote = CREDS_BASE / sanitize_ip_for_path(host) / "proxy.yml"
    if remote.is_file():
        return remote
    local = SERVER_CREDS_DIR / "proxy.yml"
    try:
        if local.is_file():
            creds = ServerCredentials.load(local)
            if creds.server.ip == host:
                return local
    except (PermissionError, OSError):
        pass
    return None


def _find_exit_for_relay(relay_ip: str, *, force_refresh: bool = False) -> tuple[ServerRegistry, ResolvedServer] | None:
    """Find which exit server a relay belongs to by scanning credentials."""
    registry = ServerRegistry(SERVERS_FILE)
    for entry in registry.list():
        proxy_file = _find_proxy_file(entry.host)
        if proxy_file is None:
            continue
        creds = ServerCredentials.load(proxy_file)
        for relay in creds.relays:
            if relay.ip == relay_ip:
                resolved = resolve_server(registry, explicit_ip=entry.host, user=entry.user)
                resolved = ensure_server_connection(resolved)
                fetch_credentials(resolved, force=force_refresh)
                return registry, resolved
    return None


def _relay_registry_user(registry: ServerRegistry, relay_ip: str, explicit_user: str) -> str:
    """Pick the relay SSH user from explicit flag or the stored registry entry."""
    if explicit_user:
        return explicit_user
    entry = registry.find(relay_ip)
    if entry and entry.user:
        return entry.user
    return "root"


def _save_relay_local(relay_ip: str, exit_ip: str, exit_port: int, listen_port: int) -> None:
    """Save relay metadata to ~/.meridian/credentials/<relay-ip>/relay.yml."""
    import os
    import tempfile

    relay_creds_dir = CREDS_BASE / sanitize_ip_for_path(relay_ip)
    relay_creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    relay_meta = {
        "role": "relay",
        "exit_ip": exit_ip,
        "exit_port": exit_port,
        "listen_port": listen_port,
    }
    relay_file = relay_creds_dir / "relay.yml"
    # Atomic write: tempfile + rename
    fd, tmp = tempfile.mkstemp(dir=str(relay_creds_dir), suffix=".tmp")
    try:
        os.write(fd, yaml.dump(relay_meta, default_flow_style=False, sort_keys=False).encode())
        os.close(fd)
        fd = -1
        os.chmod(tmp, 0o600)
        os.rename(tmp, str(relay_file))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _sync_exit_credentials_to_server(resolved_exit: ResolvedServer) -> bool:
    """Sync exit server credentials back to /etc/meridian/ after relay changes.

    Uses conn.write_file() which handles sudo for non-root users — SCP alone
    can't write to root-owned /etc/meridian/.
    """
    if resolved_exit.local_mode:
        return True
    try:
        for path in resolved_exit.creds_dir.iterdir():
            if not path.is_file():
                continue
            if not resolved_exit.conn.write_file(path, f"/etc/meridian/{path.name}"):
                return False
        return True
    except OSError:
        return False


def _refresh_exit_credentials_or_fail(resolved_exit: ResolvedServer, *, action: str) -> None:
    """Force-refresh exit credentials before mutating relay state."""
    if fetch_credentials(resolved_exit, force=True):
        return
    fail(
        f"Could not refresh exit credentials before {action}",
        hint="Check SSH/SCP and retry. Meridian will not mutate cached relay state without a fresh server read.",
        hint_type="system",
    )


def _save_exit_credentials_with_sync(
    resolved_exit: ResolvedServer,
    creds: ServerCredentials,
    *,
    recovery_hint: str,
) -> None:
    """Persist exit credentials and fail closed if sync back to the server fails."""
    proxy_file = resolved_exit.creds_dir / "proxy.yml"
    original_bytes = proxy_file.read_bytes() if proxy_file.exists() else None

    creds.save(proxy_file)
    if _sync_exit_credentials_to_server(resolved_exit):
        return

    if original_bytes is None:
        proxy_file.unlink(missing_ok=True)
    else:
        proxy_file.write_bytes(original_bytes)
        proxy_file.chmod(0o600)

    fail(
        "Could not sync updated relay credentials to exit server",
        hint=recovery_hint,
        hint_type="system",
    )


def _regenerate_client_pages(
    resolved_exit: ResolvedServer,
    creds: ServerCredentials,
) -> None:
    """Regenerate connection pages for all clients after relay topology change."""
    from meridian.render import save_connection_html
    from meridian.urls import build_all_relay_urls, build_protocol_urls, generate_qr_base64

    for client in creds.clients:
        protocol_urls = build_protocol_urls(
            name=client.name,
            reality_uuid=client.reality_uuid,
            wss_uuid=client.wss_uuid,
            creds=creds,
            server_name=creds.branding.server_name,
        )
        relay_url_sets = build_all_relay_urls(
            client.name,
            client.reality_uuid,
            client.wss_uuid,
            creds,
            server_name=creds.branding.server_name,
        )

        server_ip = creds.server.ip or resolved_exit.ip
        domain = creds.server.domain or ""
        file_prefix = f"{resolved_exit.ip}-{client.name}"

        # Regenerate local HTML file
        save_connection_html(
            protocol_urls,
            resolved_exit.creds_dir / f"{file_prefix}-connection-info.html",
            server_ip,
            domain=domain,
            client_name=client.name,
            relay_entries=relay_url_sets,
        )

        # Regenerate server-hosted PWA page (if enabled)
        if creds.server.hosted_page and creds.panel.info_page_path and client.reality_uuid:
            from dataclasses import replace as dc_replace

            from meridian.pwa import generate_client_files, upload_client_files

            urls_with_qr = [dc_replace(p, qr_b64=generate_qr_base64(p.url)) if p.url else p for p in protocol_urls]

            host = domain or server_ip
            page_url = f"https://{host}/{creds.panel.info_page_path}/{client.reality_uuid}/"

            client_files = generate_client_files(
                urls_with_qr,
                server_ip=server_ip,
                domain=domain,
                client_name=client.name,
                relay_entries=relay_url_sets,
                page_url=page_url,
                server_name=creds.branding.server_name,
                server_icon=creds.branding.icon,
                color=creds.branding.color,
            )
            upload_error = upload_client_files(resolved_exit.conn, client.reality_uuid, client_files)
            if upload_error:
                from meridian.console import warn

                warn(f"Could not update connection page for client '{client.name}'")


# ---------------------------------------------------------------------------
# Relay Deploy
# ---------------------------------------------------------------------------


def run_deploy(
    relay_ip: str,
    exit_arg: str,
    user: str = "",
    relay_name: str = "",
    listen_port: int = 443,
    yes: bool = False,
    sni: str = "",
    ssh_port: int = 22,
    exit_user: str = "",
) -> None:
    """Deploy a relay node that forwards traffic to an exit server."""
    # Validate relay IP
    if not is_ip(relay_ip):
        fail(f"Invalid relay IP: {relay_ip}", hint="Enter a valid IP address", hint_type="user")

    if relay_name and not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", relay_name):
        fail(f"Invalid relay name: {relay_name}", hint="Use letters, numbers, hyphens, underscores", hint_type="user")

    registry = ServerRegistry(SERVERS_FILE)
    relay_user = _relay_registry_user(registry, relay_ip, user)

    # --- Explain what a relay does ---
    err_console.print()
    err_console.print("  [bold]What is a relay?[/bold]")
    err_console.print("  [dim]A relay is a lightweight server inside your country that forwards[/dim]")
    err_console.print("  [dim]traffic to an exit server abroad. Censors see only domestic traffic.[/dim]")
    err_console.print("  [dim]The relay runs Realm (a fast TCP forwarder) — no VPN software needed.[/dim]")
    err_console.print("  [dim]All encryption remains end-to-end between client and exit server.[/dim]")
    err_console.print()

    # Resolve exit server
    info(f"Resolving exit server: {exit_arg}")
    resolved_exit = _resolve_exit(registry, exit_arg, exit_user, force_refresh=True)
    exit_creds = ServerCredentials.load(resolved_exit.creds_dir / "proxy.yml")

    # Check if relay already registered
    for relay in exit_creds.relays:
        if relay.ip == relay_ip:
            fail(
                f"Relay {relay_ip} is already attached to exit {resolved_exit.ip}",
                hint=f"To re-deploy, remove first: meridian relay remove {relay_ip} --exit {resolved_exit.ip}",
                hint_type="user",
            )

    # Same-server warning (exit and relay on same IP)
    if relay_ip == resolved_exit.ip:
        if listen_port == 443:
            fail(
                "Relay and exit are the same server — port 443 is already in use by the exit",
                hint="Use a different port: meridian relay deploy "
                f"{relay_ip} --exit {resolved_exit.ip} --port 8443\n"
                "  This is useful for testing but not for production.",
                hint_type="user",
            )
        warn(
            f"Relay and exit are the same server ({relay_ip}). "
            "This is fine for testing but won't bypass IP blocks in production."
        )

    ok(f"Exit server verified: {resolved_exit.ip}")

    # Connect to relay server
    info(f"Connecting to relay server: {relay_user}@{relay_ip}")
    relay_conn = ServerConnection(ip=relay_ip, user=relay_user, port=ssh_port)
    try:
        relay_conn.check_ssh()
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.hint_type)
    ok("SSH connection to relay established")

    # Check if relay port is already in use
    port_check = relay_conn.run(f"ss -tlnp sport = :{listen_port} 2>/dev/null", timeout=10)
    if port_check.returncode == 0 and f":{listen_port}" in port_check.stdout:
        # Extract process name from ss output for a helpful message
        ss_lines = port_check.stdout.strip().splitlines()
        process_name = ""
        process_info = ""
        for ss_line in ss_lines[1:]:  # skip header
            if f":{listen_port}" in ss_line:
                if "users:" in ss_line:
                    process_info = ss_line.split("users:")[1].strip().strip("()")
                    match = re.search(r'"([^"]*)"', process_info)
                    if match:
                        process_name = match.group(1)
                break

        if process_name == "realm":
            # Previous relay still running (e.g. after exit teardown + re-deploy)
            warn(f"Previous relay service found on port {listen_port} — stopping it")
            relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
            relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
            ok("Previous relay service stopped")
        else:
            msg = f"Port {listen_port} is already in use"
            if process_info:
                msg += f" by {process_info}"
            fail(
                msg,
                hint=f"Choose a different port: meridian relay deploy {relay_ip} --exit {exit_arg} --port <OTHER_PORT>",
                hint_type="system",
            )

    # Test relay -> exit connectivity (exit always listens on 443)
    import shlex

    info("Testing relay -> exit connectivity...")
    q_exit_ip = shlex.quote(resolved_exit.ip)
    tcp_test = relay_conn.run(
        f"nc -z -w 5 {q_exit_ip} 443 2>/dev/null",
        timeout=10,
    )
    if tcp_test.returncode != 0:
        warn(f"Relay cannot reach exit {resolved_exit.ip}:443 — will attempt deployment anyway")
    else:
        ok("Relay -> exit connectivity confirmed")

    # Determine relay SNI target
    relay_sni = sni  # from --sni flag
    if not relay_sni:
        # Scan for optimal SNI from the relay's network perspective
        from meridian.commands.scan import scan_for_sni

        err_console.print()
        err_console.print("  [bold]SNI Scanner[/bold]")
        err_console.print("  [dim]Finding optimal Reality SNI target near the relay server...[/dim]")
        err_console.print("  [dim]This makes the relay look like it hosts a nearby website.[/dim]")
        err_console.print()

        candidates = scan_for_sni(relay_conn, relay_ip)

        if candidates:
            from meridian.console import choose

            err_console.print(f"  Found {len(candidates)} SNI targets near relay:\n")
            # Show top candidates (max 8)
            display = candidates[:8]
            choices = [f"{d}" for d in display]
            choices.append("Abort (rerun with --sni)")
            choice = choose("Select camouflage target for relay", choices, default=1)
            if choice <= len(display):
                relay_sni = display[choice - 1]
                ok(f"Relay SNI target: {relay_sni}")
            else:
                fail(
                    "Relay deploy requires a relay-local SNI target",
                    hint="Re-run and choose one of the scanned targets, or pass --sni explicitly.",
                    hint_type="user",
                )
        else:
            fail(
                "Could not find a relay-local SNI target",
                hint="Pass --sni explicitly for this relay, or retry from a relay network with scannable targets.",
                hint_type="system",
            )
        err_console.print()

    # Show deployment summary
    from rich.panel import Panel

    from meridian.config import REALM_VERSION

    summary = (
        f"Relay:    {relay_user}@{relay_ip}:{listen_port}\n"
        f"Exit:     {resolved_exit.ip}:443\n"
        f"Engine:   Realm v{REALM_VERSION} (zero-copy TCP forwarder)\n"
        f"Name:     {relay_name or '(auto)'}\n"
        f"SNI:      {relay_sni} (relay camouflage target)\n"
        f"\n"
        f"How it works:\n"
        f"  Client -> {relay_ip}:{listen_port} -> "
        f"{resolved_exit.ip}:443 -> Internet\n"
        f"  Censors see: traffic to {relay_sni} from {relay_ip}\n"
        f"  Encryption: end-to-end (relay cannot read content)"
    )
    err_console.print()
    err_console.print(Panel(summary, title="[bold]Relay deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()

    if not yes:
        confirm(f"Deploy relay to {relay_user}@{relay_ip}?")
    err_console.print()

    # Run relay provisioner
    from meridian.provision.relay import RelayContext, build_relay_steps
    from meridian.provision.steps import Provisioner

    ctx = RelayContext(
        relay_ip=relay_ip,
        exit_ip=resolved_exit.ip,
        exit_port=443,
        listen_port=listen_port,
        user=relay_user,
    )

    info(f"Configuring relay at {relay_ip}...")
    err_console.print()

    provisioner = Provisioner(build_relay_steps(ctx))
    results = provisioner.run(relay_conn, ctx)

    # Check for failures
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail(
            "Relay deployment failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}",
            hint_type="system",
        )

    err_console.print()
    ok("Relay deployed successfully")

    # Create relay-specific Xray inbound on exit server (for per-relay SNI).
    # When relay uses the same SNI as exit, no separate inbound or nginx
    # routing is needed — the existing xray_reality inbound handles it.
    exit_sni = exit_creds.server.sni or DEFAULT_SNI
    if relay_sni and relay_sni != exit_sni:
        info("Creating relay-specific Xray inbound on exit server...")
        inbound_ok = _create_relay_inbound(
            resolved_exit.conn,
            exit_creds,
            relay_sni,
            relay_ip,
            relay_name,
        )
        if not inbound_ok:
            fail(
                "Relay inbound creation failed on the exit server",
                hint=(
                    "The relay node was provisioned, but Meridian did not save relay state locally. "
                    "Fix the exit panel and retry."
                ),
                hint_type="system",
            )
        if not _deploy_relay_nginx(resolved_exit.conn, relay_sni, relay_ip, relay_name):
            fail(
                "Relay nginx routing update failed on the exit server",
                hint=(
                    "The relay node was provisioned, but Meridian did not save relay state locally. "
                    "Fix nginx on the exit and retry."
                ),
                hint_type="system",
            )

    # Update exit credentials with new relay entry
    relay_entry = RelayEntry(
        ip=relay_ip,
        name=relay_name,
        port=listen_port,
        added=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sni=relay_sni,
    )
    exit_creds.relays.append(relay_entry)
    _save_exit_credentials_with_sync(
        resolved_exit,
        exit_creds,
        recovery_hint=(
            f"The relay may already be provisioned remotely. Once SSH/SCP works, rerun: "
            f"meridian relay deploy {relay_ip} --exit {resolved_exit.ip}"
        ),
    )

    # Save relay metadata locally
    _save_relay_local(relay_ip, resolved_exit.ip, 443, listen_port)

    # Register relay in server registry
    if relay_ip != resolved_exit.ip:
        registry.add(
            ServerEntry(host=relay_ip, user=relay_user, name=relay_name, role=SERVER_ROLE_RELAY, port=ssh_port)
        )

    # Regenerate connection pages for all existing clients
    if exit_creds.clients:
        info("Regenerating client connection pages...")
        _regenerate_client_pages(resolved_exit, exit_creds)
        ok(f"Updated {len(exit_creds.clients)} client page(s)")

    # Print success
    err_console.print()
    err_console.print("  [ok][bold]Done![/bold][/ok]")
    err_console.print()
    ok(f"Relay {relay_ip} is forwarding to exit {resolved_exit.ip}")
    err_console.print()

    err_console.print("  [bold]How clients connect now:[/bold]")
    err_console.print(
        f"  [dim]Client -> {relay_ip}:{listen_port} (domestic) -> {resolved_exit.ip}:443 (abroad) -> Internet[/dim]"
    )
    err_console.print(f"  [dim]Censors only see traffic to {relay_ip} — a domestic IP.[/dim]")
    err_console.print()

    err_console.print("  [bold]What changed for existing clients:[/bold]")
    if exit_creds.clients:
        n = len(exit_creds.clients)
        err_console.print(f"  [dim]All {n} client page(s) now show relay connection as recommended.[/dim]")
        err_console.print("  [dim]Direct connection URLs are still available as backup.[/dim]")
    else:
        err_console.print("  [dim]No clients yet. When you add them, relay URLs will be included automatically.[/dim]")
    err_console.print()

    err_console.print("  [bold]Next steps:[/bold]\n")
    err_console.print("  [ok]1.[/ok] Add a client (relay URLs included automatically):")
    err_console.print(f"     [info]meridian client add alice --server {resolved_exit.ip}[/info]\n")
    err_console.print("  [ok]2.[/ok] Check relay health:")
    err_console.print(f"     [info]meridian relay check {relay_ip}[/info]\n")
    err_console.print("  [ok]3.[/ok] List all relays:")
    err_console.print("     [info]meridian relay list[/info]\n")
    err_console.print()
    line()


# ---------------------------------------------------------------------------
# Relay List
# ---------------------------------------------------------------------------


def run_list(
    exit_arg: str = "",
    user: str = "",
    exit_user: str = "",
) -> None:
    """List relay nodes attached to exit server(s)."""
    from rich.box import ROUNDED
    from rich.table import Table

    registry = ServerRegistry(SERVERS_FILE)

    if exit_arg:
        # List relays for a specific exit
        resolved = _resolve_exit(registry, exit_arg, exit_user)
        creds = ServerCredentials.load(resolved.creds_dir / "proxy.yml")

        if not creds.relays:
            info(f"No relays attached to {resolved.ip}")
            err_console.print(f"\n  [dim]Add one: meridian relay deploy RELAY_IP --exit {resolved.ip}[/dim]\n")
            return

        table = Table(
            title=f"Relays for {resolved.ip}",
            show_lines=False,
            pad_edge=False,
            box=ROUNDED,
            padding=(0, 2),
        )
        table.add_column("IP", style="bold cyan")
        table.add_column("Name", style="dim")
        table.add_column("Port", justify="right")
        table.add_column("Added", style="dim")

        for relay in creds.relays:
            table.add_row(relay.ip, relay.name or "-", str(relay.port), relay.added[:10])

        err_console.print()
        err_console.print(table)
        err_console.print()
        err_console.print(f"  [dim]Total: {len(creds.relays)} relay(s)[/dim]\n")
        return

    # List all relays across all exits
    all_relays: list[tuple[str, RelayEntry]] = []
    for entry in registry.list():
        proxy_file = _find_proxy_file(entry.host)
        if proxy_file is None:
            continue
        creds = ServerCredentials.load(proxy_file)
        for relay in creds.relays:
            all_relays.append((entry.host, relay))

    if not all_relays:
        info("No relay nodes configured")
        err_console.print("\n  [dim]Deploy one: meridian relay deploy RELAY_IP --exit EXIT_IP[/dim]\n")
        return

    table = Table(
        title="All Relay Nodes",
        show_lines=False,
        pad_edge=False,
        box=ROUNDED,
        padding=(0, 2),
    )
    table.add_column("Relay IP", style="bold cyan")
    table.add_column("Name", style="dim")
    table.add_column("Exit IP", style="bold")
    table.add_column("Port", justify="right")
    table.add_column("Added", style="dim")

    for exit_ip, relay in all_relays:
        table.add_row(relay.ip, relay.name or "-", exit_ip, str(relay.port), relay.added[:10])

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {len(all_relays)} relay(s)[/dim]\n")


# ---------------------------------------------------------------------------
# Relay Remove
# ---------------------------------------------------------------------------


def run_remove(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
    yes: bool = False,
    exit_user: str = "",
) -> None:
    """Remove a relay node."""
    if not is_ip(relay_ip):
        fail(f"Invalid relay IP: {relay_ip}", hint="Enter a valid IP address", hint_type="user")

    registry = ServerRegistry(SERVERS_FILE)

    # Find the exit server for this relay
    if exit_arg:
        resolved_exit = _resolve_exit(registry, exit_arg, exit_user, force_refresh=True)
    else:
        result = _find_exit_for_relay(relay_ip, force_refresh=True)
        if result is None:
            fail(
                f"Relay {relay_ip} not found in any exit server's configuration",
                hint="Specify exit with --exit, or check: meridian relay list",
                hint_type="user",
            )
        registry, resolved_exit = result

    _refresh_exit_credentials_or_fail(resolved_exit, action=f"removing relay {relay_ip}")
    exit_creds = ServerCredentials.load(resolved_exit.creds_dir / "proxy.yml")

    # Find relay entry
    relay_entry = next((r for r in exit_creds.relays if r.ip == relay_ip), None)
    if relay_entry is None:
        fail(
            f"Relay {relay_ip} is not attached to exit {resolved_exit.ip}",
            hint="Check: meridian relay list",
            hint_type="user",
        )

    if not yes:
        relay_label = relay_entry.name or relay_ip
        confirm(f"Remove relay {relay_label} from exit {resolved_exit.ip}?")

    relay_user = _relay_registry_user(registry, relay_ip, user)

    # Stop service on relay
    info(f"Stopping relay service on {relay_ip}...")
    try:
        relay_conn = ServerConnection(ip=relay_ip, user=relay_user)
        relay_conn.check_ssh()
        relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
        relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
        ok("Relay service stopped")
    except Exception:
        warn(f"Could not connect to relay {relay_ip} — service may still be running")

    # Clean up relay-specific Xray inbound and nginx config on exit server
    if relay_entry.sni:
        info("Removing relay Xray inbound from exit server...")
        remark = _relay_inbound_remark(relay_entry)
        try:
            from meridian.panel import PanelClient, PanelError

            panel = PanelClient(
                conn=resolved_exit.conn,
                panel_port=exit_creds.panel.port,
                web_base_path=exit_creds.panel.web_base_path or "",
            )
            panel.login(exit_creds.panel.username or "", exit_creds.panel.password or "")
            with panel:
                ib = panel.find_inbound(remark)
                if ib is not None:
                    panel.api_post_empty(f"/panel/api/inbounds/del/{ib.id}")
                    ok(f"Relay inbound '{remark}' deleted")
                else:
                    info("Relay inbound not found on panel (already removed)")
        except (PanelError, Exception) as e:
            warn(f"Could not remove relay inbound: {e}")

        info("Removing relay nginx config...")
        if not _remove_relay_nginx(resolved_exit.conn, relay_entry):
            fail(
                "Relay nginx routing cleanup failed on the exit server",
                hint="Meridian did not forget the relay locally. Fix nginx on the exit and retry.",
                hint_type="system",
            )

    # Remove relay from exit credentials
    exit_creds.relays = [r for r in exit_creds.relays if r.ip != relay_ip]
    _save_exit_credentials_with_sync(
        resolved_exit,
        exit_creds,
        recovery_hint=(
            f"The relay may already be partially removed remotely. Once SSH/SCP works, rerun: "
            f"meridian relay remove {relay_ip} --exit {resolved_exit.ip}"
        ),
    )
    ok(f"Relay {relay_ip} removed from exit configuration")

    # Clean up local relay credentials
    relay_creds_dir = CREDS_BASE / sanitize_ip_for_path(relay_ip)
    relay_file = relay_creds_dir / "relay.yml"
    if relay_file.exists():
        relay_file.unlink()

    # Remove from server registry
    if relay_ip != resolved_exit.ip:
        registry.remove(relay_ip)

    # Regenerate connection pages
    if exit_creds.clients:
        info("Regenerating client connection pages...")
        _regenerate_client_pages(resolved_exit, exit_creds)
        ok(f"Updated {len(exit_creds.clients)} client page(s)")

    err_console.print(f"\n  Relay {relay_ip} has been removed.\n")


# ---------------------------------------------------------------------------
# Relay Check
# ---------------------------------------------------------------------------


def run_check(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
    exit_user: str = "",
) -> None:
    """Check health of a relay node."""
    if not is_ip(relay_ip):
        fail(f"Invalid relay IP: {relay_ip}", hint="Enter a valid IP address", hint_type="user")

    registry = ServerRegistry(SERVERS_FILE)

    # Find exit server
    if exit_arg:
        resolved_exit = _resolve_exit(registry, exit_arg, exit_user)
    else:
        result = _find_exit_for_relay(relay_ip)
        if result is None:
            fail(
                f"Relay {relay_ip} not found",
                hint="Specify exit with --exit, or check: meridian relay list",
                hint_type="user",
            )
        registry, resolved_exit = result

    exit_creds = ServerCredentials.load(resolved_exit.creds_dir / "proxy.yml")

    relay_entry = next((r for r in exit_creds.relays if r.ip == relay_ip), None)
    if relay_entry is None:
        fail(f"Relay {relay_ip} not attached to exit {resolved_exit.ip}", hint_type="user")

    relay_label = relay_entry.name or relay_ip
    info(f"Checking relay: {relay_label} -> exit: {resolved_exit.ip}")
    err_console.print()

    all_ok = True
    relay_user = _relay_registry_user(registry, relay_ip, user)

    # 1. SSH connectivity to relay
    try:
        relay_conn = ServerConnection(ip=relay_ip, user=relay_user)
        relay_conn.check_ssh()
        ok("SSH to relay: connected")
    except Exception:
        err_console.print("  [red bold]\u2717[/red bold] SSH to relay: failed")
        err_console.print(f"    [dim]Cannot connect to {relay_ip} via SSH[/dim]")
        err_console.print()
        warn("Cannot proceed without SSH — check SSH key and user")
        err_console.print()
        return

    # 2. Realm service status
    status = relay_conn.run(f"systemctl is-active {RELAY_SERVICE_NAME}", timeout=10)
    if status.returncode == 0 and status.stdout.strip() == "active":
        ok("Realm service: active")
    else:
        err_console.print(f"  [red bold]\u2717[/red bold] Realm service: {status.stdout.strip() or 'not found'}")
        all_ok = False

    # 3. Relay -> exit TCP connectivity
    import shlex as _shlex

    q_exit = _shlex.quote(resolved_exit.ip)
    tcp_test = relay_conn.run(
        f"nc -z -w 5 {q_exit} 443 2>/dev/null",
        timeout=10,
    )
    if tcp_test.returncode == 0:
        ok(f"Relay -> exit TCP: reachable ({resolved_exit.ip}:443)")
    else:
        err_console.print("  [red bold]\u2717[/red bold] Relay -> exit TCP: unreachable")
        all_ok = False

    # 4. Local -> relay TCP connectivity (port 443)
    from meridian.ssh import tcp_connect

    if tcp_connect(relay_ip, relay_entry.port):
        ok(f"Local -> relay TCP: reachable ({relay_ip}:{relay_entry.port})")
    else:
        err_console.print("  [red bold]\u2717[/red bold] Local -> relay TCP: unreachable")
        all_ok = False

    err_console.print()
    if all_ok:
        ok("All checks passed")
    else:
        warn("Some checks failed — see details above")
    err_console.print()
