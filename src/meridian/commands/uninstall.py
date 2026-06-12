"""Remove proxy from server."""

from __future__ import annotations

import shutil

import typer

from meridian.commands.resolve import (
    ensure_server_connection,
    fetch_credentials,
    resolve_server,
    try_resolve_server,
)
from meridian.config import CREDS_BASE, RELAY_SERVICE_NAME, SERVERS_FILE, sanitize_ip_for_path
from meridian.console import err_console, fail, info, ok, prompt, warn
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection
from meridian.ssh_ui import RichSSHUI


def run(
    ip: str = "",
    user: str = "root",
    yes: bool = False,
    requested_server: str = "",
) -> None:
    """Uninstall Meridian from a server."""
    registry = ServerRegistry(SERVERS_FILE)

    # If no IP given and resolution fails, prompt for it
    if not ip:
        resolved = try_resolve_server(registry, requested_server=requested_server, user=user)
        if not resolved:
            server_ip = prompt("Server IP to uninstall from")
            if not server_ip:
                fail("Server IP is required for uninstall", hint_type="user")
            resolved = resolve_server(registry, explicit_ip=server_ip, user=user)
    else:
        resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    # Confirmation
    err_console.print()
    warn(f"This will remove Meridian from {resolved.ip}.")
    warn("Docker and system packages will NOT be touched.")
    err_console.print()

    if not yes:
        answer = prompt("Continue? (y/N)")
        if answer.lower() != "y":
            info("Cancelled.")
            raise typer.Exit()
    err_console.print()

    resolved = ensure_server_connection(resolved)
    fetch_credentials(resolved)

    info(f"Removing Meridian from {resolved.ip}...")
    err_console.print()

    # Stop relay nodes that forward to this exit
    from meridian.cluster import ClusterConfig

    cluster = ClusterConfig.load()
    relays_for_node = [r for r in cluster.relays if r.exit_node_ip == resolved.ip]
    if relays_for_node:
        info(f"Stopping {len(relays_for_node)} relay node(s)...")
        for relay in relays_for_node:
            try:
                relay_conn = ServerConnection(ip=relay.ip, user=user)
                relay_conn.check_ssh(ui=RichSSHUI())
                relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
                relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
                ok(f"Relay {relay.ip} stopped")
            except (OSError, RuntimeError):
                warn(f"Could not reach relay {relay.ip} — service may still be running")
            # Clean up local relay metadata
            relay_creds_dir = CREDS_BASE / sanitize_ip_for_path(relay.ip)
            relay_file = relay_creds_dir / "relay.yml"
            if relay_file.exists():
                relay_file.unlink()
            registry.remove(relay.ip)
        err_console.print()

    # Run uninstall via provisioner
    from meridian.provision.steps import ProvisionContext, Provisioner
    from meridian.provision.uninstall import Uninstall

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        creds_dir=str(resolved.creds_dir),
    )

    provisioner = Provisioner([Uninstall()])
    results = provisioner.run(resolved.conn, ctx)

    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail("Uninstall failed", hint=failed[0].detail, hint_type="system")

    # Remove from server registry
    registry.remove(resolved.ip)

    # Remove local credentials
    creds_dir = CREDS_BASE / sanitize_ip_for_path(resolved.ip)
    if creds_dir.exists():
        shutil.rmtree(creds_dir)

    # Clean up cluster.yml
    from meridian.config import CLUSTER_CONFIG

    if cluster.is_configured:
        node = cluster.find_node(resolved.ip)
        if node and node.is_panel_host:
            # Panel host torn down — entire cluster config is invalid
            CLUSTER_CONFIG.unlink(missing_ok=True)
            info("Removed cluster.yml (panel host was torn down)")
        elif node:
            # Non-panel node — remove just that entry
            cluster.remove_node(resolved.ip)
            cluster.save()
            info(f"Removed node {resolved.ip} from cluster.yml")

    err_console.print()
    ok("Uninstall complete.")
    err_console.print()
    err_console.print(f"  [dim]To redeploy: meridian deploy {resolved.ip}[/dim]")
    err_console.print()
