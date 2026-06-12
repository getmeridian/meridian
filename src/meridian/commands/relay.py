"""Relay management -- deploy, list, remove, check relay nodes.

Relays are lightweight Realm TCP forwarders that provide domestic entry
points. All relay-to-panel mapping uses Remnawave Host entries; topology
is persisted in cluster.yml.
"""

from __future__ import annotations

import re
import shlex

import typer
import yaml

from meridian.cluster import ClusterConfig, ProtocolKey, RelayEntry
from meridian.commands._helpers import load_cluster, make_panel
from meridian.commands._validation import validate_command_input
from meridian.config import (
    CREDS_BASE,
    RELAY_SERVICE_NAME,
    SERVERS_FILE,
    sanitize_ip_for_path,
)
from meridian.console import confirm, err_console, fail, info, line, ok, warn
from meridian.core.command_inputs import RelayDeployRequest, RelayTargetRequest
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError


def _relay_label(relay: RelayEntry) -> str:
    """Derive a filesystem/remark-safe label from a relay entry."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", relay.name or relay.ip)


def _relay_xray_port(relay_ip: str) -> int:
    """Deterministic Xray port for a relay inbound (range 40000-49999)."""
    from meridian.core.deploy_planning import compute_relay_port

    return compute_relay_port(relay_ip)


def _relay_registry_user(registry: ServerRegistry, relay_ip: str, explicit_user: str) -> str:
    """Pick the relay SSH user from explicit flag or the stored registry entry."""
    if explicit_user:
        return explicit_user
    entry = registry.find(relay_ip)
    return entry.user if entry and entry.user else "root"


def _save_relay_local(relay_ip: str, exit_ip: str, exit_port: int, listen_port: int) -> None:
    """Save relay metadata to ~/.meridian/credentials/<relay-ip>/relay.yml (atomic)."""
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


def _find_exit_node(cluster: ClusterConfig, exit_arg: str) -> str:
    """Resolve --exit flag to a node IP. Accepts IP or node name."""
    node = cluster.find_node(exit_arg)
    if node is not None:
        return node.ip
    if not exit_arg and len(cluster.nodes) == 1:
        return cluster.nodes[0].ip
    if not exit_arg:
        fail(
            "Multiple nodes in cluster -- specify which one with --exit",
            hint="List nodes: meridian node list",
            hint_type="user",
        )
    fail(
        f"Exit node '{exit_arg}' not found in cluster",
        hint="List nodes: meridian node list",
        hint_type="user",
    )


def _deploy_relay_nginx(
    exit_conn: ServerConnection,
    relay_sni: str,
    relay_ip: str,
    relay_name: str = "",
) -> bool:
    """Create per-relay nginx SNI map + upstream on exit server and reload."""
    label = _relay_label(RelayEntry(ip=relay_ip, name=relay_name))
    port = _relay_xray_port(relay_ip)
    upstream = f"xray_relay_{label}"

    # Ensure main stream config includes relay-maps
    exit_conn.run(
        "grep -q 'relay-maps' /etc/nginx/stream.d/meridian.conf 2>/dev/null || "
        r"sed -i '/map \$ssl_preread_server_name/a\\    include /etc/nginx/stream.d/relay-maps/*.conf;' "
        "/etc/nginx/stream.d/meridian.conf",
        timeout=15,
    )
    mkdir = exit_conn.run("mkdir -p /etc/nginx/stream.d/relay-maps", timeout=15)
    if mkdir.returncode != 0:
        warn(f"could not create relay nginx map directory: {mkdir.stderr.strip() or mkdir.stdout.strip()}")
        return False
    map_write = exit_conn.put_text(
        f"/etc/nginx/stream.d/relay-maps/{label}.conf",
        f"    {relay_sni}  {upstream};\n",
        mode="644",
        timeout=15,
        operation_name="write relay nginx map",
    )
    if map_write.returncode != 0:
        warn(f"could not write relay nginx map: {map_write.stderr.strip() or map_write.stdout.strip()}")
        return False
    upstream_block = f"upstream {upstream} {{\n    server 127.0.0.1:{port};\n}}\n"
    upstream_write = exit_conn.put_text(
        f"/etc/nginx/stream.d/meridian-relay-{label}.conf",
        upstream_block,
        mode="644",
        timeout=15,
        operation_name="write relay nginx upstream",
    )
    if upstream_write.returncode != 0:
        warn(f"could not write relay nginx upstream: {upstream_write.stderr.strip() or upstream_write.stdout.strip()}")
        return False
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


def _remove_relay_nginx(exit_conn: ServerConnection, relay: RelayEntry) -> bool:
    """Remove per-relay nginx config files from the exit server and reload."""
    q = shlex.quote(_relay_label(relay))
    exit_conn.run(
        f"rm -f /etc/nginx/stream.d/relay-maps/{q}.conf /etc/nginx/stream.d/meridian-relay-{q}.conf",
        timeout=15,
    )
    if exit_conn.run("nginx -t 2>&1", timeout=15).returncode != 0:
        warn("nginx config validation failed after relay removal")
        return False
    if exit_conn.run("systemctl reload nginx", timeout=15).returncode != 0:
        warn("nginx reload failed after relay removal")
        return False
    return True


def _create_relay_hosts(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    relay_ip: str,
    relay_port: int,
    relay_sni: str,
    relay_name: str,
) -> dict[str, str]:
    """Create Remnawave Host entries for a relay. Returns {protocol_key: host_uuid}."""
    host_uuids: dict[str, str] = {}
    label = _relay_label(RelayEntry(ip=relay_ip, name=relay_name))

    # Panel v2.7+ only accepts DEFAULT/TLS/NONE for securityLayer.
    # Reality hosts use "DEFAULT" (panel infers reality from inbound type).
    _PROTO_CONFIG: list[tuple[ProtocolKey, str]] = [
        (ProtocolKey.REALITY, "DEFAULT"),
        (ProtocolKey.XHTTP, "TLS"),
    ]
    for proto_key, security in _PROTO_CONFIG:
        ref = cluster.get_inbound(proto_key)
        if not ref or not ref.uuid:
            continue
        remark = f"Relay-{label}-{proto_key}"
        existing = panel.find_host_by_remark(remark)
        if existing:
            host_uuids[str(proto_key)] = existing.uuid
            info(f"Host '{remark}' already exists, reusing")
            continue
        try:
            host = panel.create_host(
                remark=remark,
                address=relay_ip,
                port=relay_port,
                config_profile_uuid=cluster.config_profile_uuid,
                inbound_uuid=ref.uuid,
                sni=relay_sni,
                fingerprint="chrome",
                security_layer=security,
            )
            host_uuids[str(proto_key)] = host.uuid
            ok(f"Host created: {remark}")
        except RemnawaveError as e:
            warn(f"Could not create {proto_key} host: {e}")
    return host_uuids


def _delete_relay_hosts(panel: MeridianPanel, relay: RelayEntry) -> None:
    """Delete all Remnawave Host entries for a relay."""
    for proto_key, host_uuid in relay.host_uuids.items():
        if not host_uuid:
            continue
        try:
            panel.delete_host(host_uuid)
            ok(f"Host deleted: {proto_key} ({host_uuid[:8]}...)")
        except RemnawaveError as e:
            warn(f"Could not delete {proto_key} host {host_uuid[:8]}...: {e}")


def run_deploy(
    relay_ip: str,
    exit_arg: str,
    user: str = "root",
    relay_name: str = "",
    listen_port: int = 443,
    yes: bool = False,
    sni: str = "",
    ssh_port: int = 22,
) -> None:
    """Deploy a relay node that forwards traffic to an exit server."""
    request = validate_command_input(
        RelayDeployRequest,
        "Invalid relay deploy request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
        relay_name=relay_name,
        listen_port=listen_port,
        yes=yes,
        sni=sni,
        ssh_port=ssh_port,
    )

    cluster = load_cluster()
    registry = ServerRegistry(SERVERS_FILE)
    exit_ip = _find_exit_node(cluster, request.exit_arg)
    exit_node = cluster.find_node(exit_ip)
    if exit_node is None:
        fail(f"Exit node {exit_ip} not found in cluster", hint_type="bug")
    if cluster.find_relay(request.relay_ip) is not None:
        fail(
            f"Relay {request.relay_ip} is already in the cluster",
            hint=f"To re-deploy, remove first: meridian relay remove {request.relay_ip}",
            hint_type="user",
        )

    # Explain relay concept
    err_console.print()
    err_console.print("  [bold]What is a relay?[/bold]")
    err_console.print("  [dim]A lightweight domestic server that forwards encrypted traffic abroad.[/dim]")
    err_console.print("  [dim]Runs Realm (TCP forwarder). Encryption is end-to-end.[/dim]")
    err_console.print()

    # Same-server warning
    if request.relay_ip == exit_ip:
        if request.listen_port == 443:
            fail(
                "Relay and exit are the same server -- port 443 is already in use",
                hint="Try: --port 8443",
                hint_type="user",
            )
        warn(f"Relay and exit are the same ({request.relay_ip}). Fine for testing, not for production.")

    ok(f"Exit node verified: {exit_ip}")

    # Connect to relay
    info(f"Connecting to relay server: {request.relay_ip}")
    relay_conn = ServerConnection(ip=request.relay_ip, user=request.user, port=request.ssh_port)
    try:
        relay_conn.check_ssh()
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.hint_type)

    ok("SSH OK")

    # Check if relay port is already in use
    port_check = relay_conn.run(f"ss -tlnp sport = :{request.listen_port} 2>/dev/null", timeout=10)
    if port_check.returncode == 0 and f":{request.listen_port}" in port_check.stdout:
        # Extract process name
        process_name = process_info = ""
        for ss_line in port_check.stdout.strip().splitlines()[1:]:
            if f":{request.listen_port}" in ss_line and "users:" in ss_line:
                process_info = ss_line.split("users:")[1].strip().strip("()")
                m = re.search(r'"([^"]*)"', process_info)
                process_name = m.group(1) if m else ""
                break
        if process_name == "realm":
            warn(f"Previous relay service found on port {request.listen_port} -- stopping it")
            relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
            relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
            ok("Previous relay service stopped")
        else:
            msg = f"Port {request.listen_port} is already in use"
            if process_info:
                msg += f" by {process_info}"
            fail(msg, hint="Try: --port <OTHER_PORT>", hint_type="system")

    # Test relay -> exit connectivity
    tcp_test = relay_conn.run(f"nc -z -w 5 {shlex.quote(exit_ip)} 443 2>/dev/null", timeout=10)
    if tcp_test.returncode != 0:
        warn(f"Relay cannot reach exit {exit_ip}:443 -- will attempt deployment anyway")
    else:
        ok("Relay -> exit connectivity confirmed")

    # Determine relay SNI target
    relay_sni = request.sni
    if not relay_sni:
        from rich.status import Status

        from meridian.commands.scan import scan_for_sni

        err_console.print()
        err_console.print("  [bold]SNI Scanner[/bold]")
        err_console.print("  [dim]Finding optimal Reality SNI target near the relay server...[/dim]")

        with Status("  [cyan]-> Scanning relay subnet...[/cyan]", console=err_console, spinner="dots"):
            candidates = scan_for_sni(relay_conn, request.relay_ip)

        if candidates:
            from meridian.console import choose

            display = candidates[:8]
            choices = [f"{d}" for d in display] + ["Abort (rerun with --sni)"]
            choice = choose("Select camouflage target for relay", choices, default=1)
            if choice <= len(display):
                relay_sni = display[choice - 1]
                ok(f"Relay SNI target: {relay_sni}")
            else:
                fail("Aborted", hint="Pass --sni explicitly.", hint_type="user")
        else:
            fail("Could not find a relay-local SNI target", hint="Pass --sni explicitly.", hint_type="system")
        err_console.print()

    # Deployment summary
    from rich.panel import Panel

    from meridian.config import REALM_VERSION

    summary = (
        f"Relay:  {request.user}@{request.relay_ip}:{request.listen_port}  |  Exit: {exit_ip}:443\n"
        f"Engine: Realm v{REALM_VERSION}  |  Name: {request.relay_name or '(auto)'}  |  SNI: {relay_sni}\n\n"
        f"  Client -> {request.relay_ip}:{request.listen_port} -> {exit_ip}:443 -> Internet\n"
        f"  Encryption: end-to-end (relay cannot read content)"
    )
    err_console.print()
    err_console.print(Panel(summary, title="[bold]Relay deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()

    if not request.yes:
        if not confirm(f"Deploy relay to {request.user}@{request.relay_ip}?"):
            raise typer.Exit(1)

    # Run relay provisioner (Realm install -- panel-agnostic)
    from meridian.provision.relay import RelayContext, build_relay_steps
    from meridian.provision.steps import Provisioner

    ctx = RelayContext(
        relay_ip=request.relay_ip,
        exit_ip=exit_ip,
        exit_port=443,
        listen_port=request.listen_port,
        user=request.user,
    )
    info(f"Configuring relay at {request.relay_ip}...")
    err_console.print()

    results = Provisioner(build_relay_steps(ctx)).run(relay_conn, ctx)
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail("Relay deployment failed", hint=f"Step '{failed[0].name}' failed: {failed[0].detail}", hint_type="system")

    err_console.print()
    ok("Realm relay deployed successfully")

    # Create Remnawave Host entries for this relay
    panel = make_panel(cluster)
    with panel:
        info("Creating relay host entries in panel...")
        host_uuids = _create_relay_hosts(
            panel,
            cluster,
            request.relay_ip,
            request.listen_port,
            relay_sni,
            request.relay_name,
        )
    if not host_uuids:
        fail(
            "No host entries created -- check that inbounds are configured",
            hint="Run: meridian fleet status",
            hint_type="system",
        )

    # Deploy nginx relay map on exit server (for per-relay SNI routing)
    if relay_sni and relay_sni != (exit_node.sni or ""):
        info("Configuring nginx SNI routing on exit server...")
        exit_conn = ServerConnection(ip=exit_ip, user=exit_node.ssh_user, port=exit_node.ssh_port)
        try:
            exit_conn.check_ssh()
        except SSHError as exc:
            fail(f"Cannot SSH to exit node {exit_ip}: {exc}", hint="Check exit node SSH access", hint_type="system")
        if not _deploy_relay_nginx(exit_conn, relay_sni, request.relay_ip, request.relay_name):
            fail(
                "Relay nginx routing update failed on the exit server",
                hint="Fix nginx on the exit and retry.",
                hint_type="system",
            )

    # Save RelayEntry to cluster.yml
    relay_entry = RelayEntry(
        ip=request.relay_ip,
        name=request.relay_name,
        port=request.listen_port,
        exit_node_ip=exit_ip,
        host_uuids=host_uuids,
        sni=relay_sni,
        ssh_user=request.user,
        ssh_port=request.ssh_port,
    )
    cluster.backup()
    cluster.relays.append(relay_entry)
    cluster.save()
    _save_relay_local(request.relay_ip, exit_ip, 443, request.listen_port)
    if request.relay_ip != exit_ip:
        registry.add(
            ServerEntry(
                host=request.relay_ip,
                user=request.user,
                name=request.relay_name,
                role=SERVER_ROLE_RELAY,
                port=request.ssh_port,
            )
        )

    # Hybrid sync — mirror the relay into desired_relays when the user manages
    # relays declaratively. Use the exit node's name when available so the
    # desired entry stays human-readable; fall back to the IP otherwise.
    from meridian.operations import hybrid_sync_desired_relays_add

    exit_node_for_sync = cluster.find_node(exit_ip)
    exit_ref = exit_node_for_sync.name if exit_node_for_sync and exit_node_for_sync.name else exit_ip
    hybrid_sync_desired_relays_add(cluster, relay_entry, exit_node_ref=exit_ref)

    ok("Relay saved to cluster")

    # Success output
    err_console.print()
    ok(f"Relay {request.relay_ip} forwarding to exit {exit_ip}")
    relay_route = f"Client -> {request.relay_ip}:{request.listen_port} (domestic) -> {exit_ip}:443 (abroad) -> Internet"
    err_console.print(f"  [dim]{relay_route}[/dim]")
    err_console.print("  [dim]Subscriptions auto-update -- clients get relay URLs on next sync.[/dim]")
    err_console.print()
    err_console.print("  [bold]Next steps:[/bold]")
    err_console.print("    meridian client add alice          [dim]# relay URLs included[/dim]")
    err_console.print(f"    meridian relay check {request.relay_ip}    [dim]# verify relay health[/dim]")
    err_console.print("    meridian relay list                [dim]# list all relays[/dim]")
    err_console.print()
    line()


def run_list(
    exit_arg: str = "",
    user: str = "",
) -> None:
    """List relay nodes from cluster configuration."""
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_cluster()

    relays = cluster.relays
    if exit_arg:
        exit_ip = _find_exit_node(cluster, exit_arg)
        relays = [r for r in relays if r.exit_node_ip == exit_ip]

    if not relays:
        info(f"No relays {'attached to exit ' + exit_arg if exit_arg else 'configured'}")
        err_console.print("\n  [dim]Deploy one: meridian relay deploy RELAY_IP --exit EXIT_IP[/dim]\n")
        return

    # Optionally check host status from panel
    host_status: dict[str, bool | None] = {}
    try:
        with make_panel(cluster) as panel:
            host_map = {h.uuid: h for h in panel.list_hosts()}
            for relay in relays:
                for _, host_uuid in relay.host_uuids.items():
                    h = host_map.get(host_uuid)
                    if h and (relay.ip not in host_status or not host_status[relay.ip]):
                        host_status[relay.ip] = not h.is_disabled
    except RemnawaveError:
        pass

    # JSON output
    from meridian.console import is_json_mode

    if is_json_mode():
        from meridian.console import json_output

        relays_data = []
        for relay in relays:
            enabled = host_status.get(relay.ip)
            relays_data.append(
                {
                    "ip": relay.ip,
                    "name": relay.name,
                    "exit_node_ip": relay.exit_node_ip,
                    "port": relay.port,
                    "sni": relay.sni,
                    "enabled": enabled,
                }
            )
        json_output({"relays": relays_data})
        return

    title = f"Relays for {exit_arg}" if exit_arg else "All Relay Nodes"
    table = Table(title=title, show_lines=False, pad_edge=False, box=ROUNDED, padding=(0, 2))
    for col, kw in [
        ("Relay IP", {"style": "bold cyan"}),
        ("Name", {"style": "dim"}),
        ("Exit", {"style": "bold"}),
        ("Port", {"justify": "right"}),
        ("SNI", {"style": "dim"}),
        ("Status", {"justify": "center"}),
    ]:
        table.add_column(col, **kw)  # type: ignore[arg-type]

    for relay in relays:
        enabled = host_status.get(relay.ip)
        if enabled:
            status_str = "[green]enabled[/green]"
        elif enabled is False:
            status_str = "[dim]disabled[/dim]"
        else:
            status_str = "[dim]-[/dim]"
        table.add_row(
            relay.ip,
            relay.name or "-",
            relay.exit_node_ip,
            str(relay.port),
            relay.sni or "-",
            status_str,
        )

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {len(relays)} relay(s)[/dim]\n")


def run_remove(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
    yes: bool = False,
) -> None:
    """Remove a relay node."""
    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay remove request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
        yes=yes,
    )

    cluster = load_cluster()
    registry = ServerRegistry(SERVERS_FILE)

    # Find relay entry
    relay_entry = cluster.find_relay(request.relay_ip)
    if relay_entry is None:
        fail(f"Relay {request.relay_ip} not found in cluster", hint="Check: meridian relay list", hint_type="user")

    # Verify exit_arg matches if specified
    if request.exit_arg:
        if relay_entry.exit_node_ip != _find_exit_node(cluster, request.exit_arg):
            fail(
                f"Relay {request.relay_ip} is attached to exit {relay_entry.exit_node_ip}, not {request.exit_arg}",
                hint_type="user",
            )

    if not request.yes:
        relay_label = relay_entry.name or request.relay_ip
        if not confirm(f"Remove relay {relay_label} from exit {relay_entry.exit_node_ip}?"):
            raise typer.Exit(1)

    relay_user = _relay_registry_user(registry, request.relay_ip, request.user)

    # Delete Remnawave hosts
    with make_panel(cluster) as panel:
        info("Removing relay host entries from panel...")
        _delete_relay_hosts(panel, relay_entry)

    # Remove nginx config from exit server
    exit_node = cluster.find_node(relay_entry.exit_node_ip)
    if relay_entry.sni and exit_node:
        info("Removing relay nginx config from exit server...")
        try:
            exit_conn = ServerConnection(ip=exit_node.ip, user=exit_node.ssh_user, port=exit_node.ssh_port)
            exit_conn.check_ssh()
            if not _remove_relay_nginx(exit_conn, relay_entry):
                warn("Relay nginx cleanup failed -- manual cleanup may be needed")
        except SSHError:
            warn(f"Could not connect to exit node {exit_node.ip} -- nginx not cleaned up")

    # Stop service on relay
    info(f"Stopping relay service on {request.relay_ip}...")
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh()
        relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
        relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
        ok("Relay service stopped")
    except (SSHError, OSError):
        warn(f"Could not connect to relay {request.relay_ip} -- service may still be running")

    # Remove from cluster.yml and local state
    cluster.relays = [r for r in cluster.relays if r.ip != request.relay_ip]
    cluster.backup()
    cluster.save()

    # Hybrid sync — drop from desired_relays (only if managed declaratively).
    from meridian.operations import hybrid_sync_desired_relays_remove

    hybrid_sync_desired_relays_remove(cluster, request.relay_ip)

    relay_file = CREDS_BASE / sanitize_ip_for_path(request.relay_ip) / "relay.yml"
    if relay_file.exists():
        relay_file.unlink()
    if request.relay_ip != relay_entry.exit_node_ip:
        registry.remove(request.relay_ip)
    ok(f"Relay {request.relay_ip} removed")
    err_console.print()


def run_check(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
) -> None:
    """Check health of a relay node."""
    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay check request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
    )

    cluster = load_cluster()
    registry = ServerRegistry(SERVERS_FILE)

    relay_entry = cluster.find_relay(request.relay_ip)
    if relay_entry is None:
        fail(f"Relay {request.relay_ip} not found in cluster", hint="Check: meridian relay list", hint_type="user")

    info(f"Checking relay: {relay_entry.name or request.relay_ip} -> exit: {relay_entry.exit_node_ip}")
    err_console.print()
    all_ok = True
    relay_user = _relay_registry_user(registry, request.relay_ip, request.user)

    # 1. SSH connectivity to relay
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh()
        ok("SSH to relay: connected")
    except (SSHError, OSError):
        err_console.print(f"  [red bold]x[/red bold] SSH to relay: failed ({request.relay_ip})")
        warn("Cannot proceed without SSH -- check SSH key and user")
        return

    # 2. Realm service status
    status = relay_conn.run(f"systemctl is-active {RELAY_SERVICE_NAME}", timeout=10)
    if status.returncode == 0 and status.stdout.strip() == "active":
        ok("Realm service: active")
    else:
        err_console.print(f"  [red bold]x[/red bold] Realm service: {status.stdout.strip() or 'not found'}")
        all_ok = False

    # 3. Relay -> exit TCP connectivity
    q_exit = shlex.quote(relay_entry.exit_node_ip)
    tcp_test = relay_conn.run(f"nc -z -w 5 {q_exit} 443 2>/dev/null", timeout=10)
    if tcp_test.returncode == 0:
        ok(f"Relay -> exit TCP: reachable ({relay_entry.exit_node_ip}:443)")
    else:
        err_console.print("  [red bold]x[/red bold] Relay -> exit TCP: unreachable")
        all_ok = False

    # 4. Local -> relay TCP connectivity
    from meridian.ssh import tcp_connect

    if tcp_connect(request.relay_ip, relay_entry.port):
        ok(f"Local -> relay TCP: reachable ({request.relay_ip}:{relay_entry.port})")
    else:
        err_console.print("  [red bold]x[/red bold] Local -> relay TCP: unreachable")
        all_ok = False

    # 5. Panel host status
    try:
        with make_panel(cluster) as panel:
            host_map = {h.uuid: h for h in panel.list_hosts()}
            for proto_key, host_uuid in relay_entry.host_uuids.items():
                host = host_map.get(host_uuid)
                if host and not host.is_disabled:
                    ok(f"Panel host ({proto_key}): enabled")
                elif host:
                    err_console.print(f"  [yellow]![/yellow] Panel host ({proto_key}): disabled")
                    all_ok = False
                else:
                    err_console.print(f"  [red bold]x[/red bold] Panel host ({proto_key}): not found")
                    all_ok = False
    except RemnawaveError:
        warn("Could not check panel host status -- panel unreachable")

    err_console.print()
    ok("All checks passed") if all_ok else warn("Some checks failed -- see above")
    err_console.print()
