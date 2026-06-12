"""Reusable provisioning operations — shared by imperative commands and reconciler.

Each function performs a complete operation (add node, remove relay, etc.)
without interactive prompts. The imperative command modules (node.py,
relay.py, client.py) handle user interaction then call these functions.
The reconciler executor calls them directly.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from meridian.cluster import ClusterConfig, NodeEntry, RelayEntry
from meridian.core.deploy_planning import compute_deploy_ports
from meridian.reconciler.snapshots import (
    hybrid_sync_desired_clients_add,
    hybrid_sync_desired_clients_remove,
    hybrid_sync_desired_nodes_add,
    hybrid_sync_desired_nodes_remove,
    hybrid_sync_desired_nodes_update,
    hybrid_sync_desired_relays_add,
    hybrid_sync_desired_relays_remove,
    load_applied_snapshot,
)
from meridian.remnawave import MeridianPanel, RemnawaveError

logger = logging.getLogger("meridian.operations")

# Re-export snapshot/hybrid-sync API so existing callers that import from
# meridian.operations keep working without changes.
__all__ = [
    "add_client",
    "add_node",
    "add_relay",
    "hybrid_sync_desired_clients_add",
    "hybrid_sync_desired_clients_remove",
    "hybrid_sync_desired_nodes_add",
    "hybrid_sync_desired_nodes_remove",
    "hybrid_sync_desired_nodes_update",
    "hybrid_sync_desired_relays_add",
    "hybrid_sync_desired_relays_remove",
    "load_applied_snapshot",
    "remove_client",
    "remove_node",
    "remove_relay",
    "update_node",
]


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------


def add_node(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    ip: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    name: str = "",
    domain: str = "",
    sni: str = "",
    harden: bool = True,
    warp: bool | None = None,
) -> NodeEntry:
    """Provision a new node and register it with the panel.

    Runs the full SSH provisioner pipeline, then configures the node via
    the panel REST API. Returns the new NodeEntry added to cluster.yml.
    """
    from meridian.config import DEFAULT_SNI
    from meridian.panel_bootstrap import run_provisioner, setup_new_node
    from meridian.resolve import ResolvedServer, ensure_server_connection
    from meridian.ssh import ServerConnection

    effective_sni = sni or DEFAULT_SNI

    # Establish SSH connection. ResolvedServer stores ip/user/local_mode/
    # creds_dir/conn — the port already lives on `conn`. We start with
    # local_mode=False; ensure_server_connection() flips it (and refreshes
    # creds_dir) if the target turns out to be the deployer's own machine.
    from meridian.config import creds_dir_for

    resolved = ResolvedServer(
        ip=ip,
        user=ssh_user,
        local_mode=False,
        creds_dir=creds_dir_for(ip, local_mode=False),
        conn=ServerConnection(ip, ssh_user, port=ssh_port),
    )
    ensure_server_connection(resolved)

    # Compute deterministic port layout
    ports = compute_deploy_ports(ip)
    xhttp_port = ports.xhttp_port
    reality_port = ports.reality_port
    wss_port = ports.wss_port
    xhttp_path = secrets.token_hex(8)
    ws_path = secrets.token_hex(8)

    # SSH provisioner pipeline (OS hardening, Docker, nginx, TLS)
    run_provisioner(
        resolved=resolved,
        cluster=cluster,
        domain=domain,
        sni=effective_sni,
        harden=harden,
        is_panel_host=False,
        secret_path=cluster.panel.secret_path,
        xhttp_port=xhttp_port,
        reality_port=reality_port,
        wss_port=wss_port,
        xhttp_path=xhttp_path,
        ws_path=ws_path,
    )

    # Panel API: register node, deploy container, create hosts
    from meridian import __version__

    setup_new_node(
        resolved=resolved,
        cluster=cluster,
        domain=domain,
        sni=effective_sni,
        reality_port=reality_port,
        xhttp_port=xhttp_port,
        wss_port=wss_port,
        version=__version__,
        xhttp_path=xhttp_path,
        ws_path=ws_path,
    )

    # Return the newly added node, applying desired name if provided
    node = cluster.find_node(ip)
    if node is None:
        raise RuntimeError(f"Node {ip} was provisioned but not found in cluster config")

    # setup_new_node uses "domain or ip" as name. Override with desired name.
    if name and name != node.name:
        node.name = name
        if node.uuid:
            try:
                panel.update_node_name(node.uuid, name)
            except RemnawaveError as e:
                logger.warning("Could not set node name in panel: %s", e)
        cluster.save()

    # Hybrid sync: if the user manages nodes declaratively, mirror this add
    # into desired_nodes so the next `meridian apply` does not see drift.
    hybrid_sync_desired_nodes_add(cluster, node, ssh_user=ssh_user, ssh_port=ssh_port)

    return node


def update_node(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    ip: str,
    name: str | None = None,
    sni: str | None = None,
    domain: str | None = None,
    warp: bool | None = None,
) -> None:
    """Redeploy an existing node with updated configuration.

    Calls the existing setup_redeploy() flow from panel_bootstrap.
    Uses None as sentinel for "not specified" (keep current).
    Empty string means "clear to default/empty".
    """
    from meridian.panel_bootstrap import setup_redeploy
    from meridian.resolve import ResolvedServer
    from meridian.ssh import ServerConnection

    node = cluster.find_node(ip)
    if node is None:
        raise ValueError(f"Node {ip} not found in cluster")

    from meridian.config import creds_dir_for

    conn = ServerConnection(ip, node.ssh_user, port=node.ssh_port)
    resolved = ResolvedServer(
        ip=ip,
        user=node.ssh_user,
        local_mode=False,
        creds_dir=creds_dir_for(ip, local_mode=False),
        conn=conn,
    )

    # Compute port layout (same scheme as deploy)
    ports = compute_deploy_ports(ip)
    xhttp_port = ports.xhttp_port
    reality_port = ports.reality_port
    wss_port = ports.wss_port

    # Save old values for rollback if redeploy fails
    old_name, old_sni, old_domain, old_warp = node.name, node.sni, node.domain, node.warp

    # Update metadata before redeploy so setup_redeploy reads new values.
    # None = keep current, "" = clear to empty/default.
    if name is not None:
        node.name = name
    if sni is not None:
        node.sni = sni
    if domain is not None:
        node.domain = domain
    effective_warp = warp if warp is not None else node.warp
    node.warp = effective_warp

    from meridian import __version__

    try:
        setup_redeploy(
            resolved=resolved,
            cluster=cluster,
            domain=node.domain,
            sni=node.sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            version=__version__,
            warp=effective_warp,
            xhttp_path=node.xhttp_path,
            ws_path=node.ws_path,
        )
    except Exception:  # Rollback metadata on failure so cluster.yml stays accurate
        node.name, node.sni, node.domain, node.warp = old_name, old_sni, old_domain, old_warp
        raise

    # Update name in panel after successful redeploy
    if name is not None and name != old_name and node.uuid:
        try:
            panel.update_node_name(node.uuid, name)
        except RemnawaveError as e:
            logger.warning("Could not update node name in panel: %s", e)

    # Hybrid sync: mirror the in-place metadata change into desired_nodes
    # (only if the user is managing nodes declaratively). If the node was
    # renamed, also rewrite matching desired_relays exit_node references.
    hybrid_sync_desired_nodes_update(cluster, node, old_name=old_name)


def remove_node(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    node_ip: str,
    force: bool = False,
) -> None:
    """Deregister a node from the panel and remove from cluster.yml.

    Handles both cluster-tracked nodes (in cluster.yml) and panel-only
    nodes (registered in panel but not in local config — e.g. stale or
    manually added nodes).
    """
    node = cluster.find_node(node_ip)

    if node is not None:
        if node.is_panel_host:
            raise ValueError("Cannot remove the panel node — use meridian teardown instead")

        # Guard: check for dependent relays
        dependent_relays = [r for r in cluster.relays if r.exit_node_ip == node.ip]
        if dependent_relays and not force:
            relay_names = ", ".join(r.name or r.ip for r in dependent_relays)
            raise ValueError(f"Cannot remove node {node.ip} — relays depend on it: {relay_names}")

    # Find node UUID — from cluster.yml or panel API
    node_uuid = node.uuid if node else ""
    if not node_uuid:
        # Panel-only node: look up by address
        api_node = panel.find_node_by_address(node_ip)
        if api_node:
            node_uuid = api_node.uuid

    # Disable + delete in panel
    if node_uuid:
        try:
            panel.disable_node(node_uuid)
        except RemnawaveError:
            logger.warning("Could not disable node %s in panel", node_ip)
        try:
            panel.delete_node(node_uuid)
        except RemnawaveError as e:
            logger.warning("Could not delete node %s from panel: %s", node_ip, e)

    # Remove from cluster.yml (no-op if panel-only node)
    cluster.nodes = [n for n in cluster.nodes if n.ip != node_ip]
    cluster.save()

    # Hybrid sync: drop from desired_nodes too (only if managed declaratively).
    hybrid_sync_desired_nodes_remove(cluster, node_ip)


# ---------------------------------------------------------------------------
# Relay operations
# ---------------------------------------------------------------------------


def add_relay(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    relay_ip: str,
    exit_node_ip: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    name: str = "",
    sni: str = "",
    port: int = 443,
) -> RelayEntry:
    """Provision a relay and register host entries in the panel.

    Reuses the same functions as the imperative ``meridian relay deploy``:
    Realm provisioner, ``create_relay_hosts()``, ``deploy_relay_nginx()``,
    and ``save_relay_local()``.
    """
    from meridian.config import DEFAULT_SNI
    from meridian.relay_ops import (
        create_relay_hosts,
        deploy_relay_nginx,
        save_relay_local,
    )
    from meridian.resolve import ResolvedServer, ensure_server_connection
    from meridian.ssh import ServerConnection

    effective_sni = sni or DEFAULT_SNI
    relay_name = name or relay_ip

    # Find exit node
    exit_node = cluster.find_node(exit_node_ip)
    if exit_node is None:
        raise ValueError(f"Exit node {exit_node_ip} not found in cluster")

    # SSH connections
    from meridian.config import creds_dir_for

    relay_conn = ServerConnection(relay_ip, ssh_user, port=ssh_port)
    relay_resolved = ResolvedServer(
        ip=relay_ip,
        user=ssh_user,
        local_mode=False,
        creds_dir=creds_dir_for(relay_ip, local_mode=False),
        conn=relay_conn,
    )
    ensure_server_connection(relay_resolved)

    exit_conn = ServerConnection(exit_node.ip, exit_node.ssh_user, port=exit_node.ssh_port)

    # Run Realm provisioner
    from meridian.provision.relay import RelayContext, build_relay_steps
    from meridian.provision.steps import Provisioner

    relay_ctx = RelayContext(
        relay_ip=relay_ip,
        exit_ip=exit_node.ip,
        exit_port=port,
        listen_port=port,
    )
    steps = build_relay_steps(relay_ctx)
    provisioner = Provisioner(steps)
    results = provisioner.run(relay_conn, relay_ctx)
    if any(r.status == "failed" for r in results):
        raise RuntimeError(f"Relay provisioning failed on {relay_ip}")

    # Create host entries — uses the same function as imperative relay deploy
    # (creates REALITY + XHTTP hosts with correct security_layer/fingerprint)
    host_uuids = create_relay_hosts(panel, cluster, relay_ip, port, effective_sni, relay_name)
    if not host_uuids:
        raise RuntimeError(f"Relay {relay_ip}: no panel hosts created")

    # Configure nginx SNI on exit node — only when relay SNI differs from
    # exit node's own SNI (same guard as imperative relay deploy).
    # Deploying nginx with the same SNI would hijack the exit node's Reality routing.
    if effective_sni and effective_sni != (exit_node.sni or ""):
        if not deploy_relay_nginx(exit_conn, effective_sni, relay_ip, relay_name):
            raise RuntimeError(f"Relay {relay_ip}: nginx configuration failed on exit node")

    # Save local relay metadata (same as imperative path)
    save_relay_local(relay_ip, exit_node.ip, port, port)

    # Save relay to cluster
    relay = RelayEntry(
        ip=relay_ip,
        name=relay_name,
        port=port,
        exit_node_ip=exit_node.ip,
        host_uuids=host_uuids,
        sni=effective_sni,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
    )
    cluster.relays.append(relay)
    cluster.save()

    # Hybrid sync: mirror into desired_relays (only if managed declaratively).
    # Use the exit node's name when available so the desired entry stays
    # human-readable; fall back to the IP when the node has no name.
    exit_node_ref = exit_node.name or exit_node.ip
    hybrid_sync_desired_relays_add(cluster, relay, exit_node_ref=exit_node_ref)

    return relay


def remove_relay(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    relay_ip: str,
) -> None:
    """Remove a relay: delete hosts, stop Realm, clean nginx, remove from cluster.yml.

    Reuses the same functions as the imperative ``meridian relay remove``.
    """
    from meridian.config import RELAY_SERVICE_NAME, sanitize_ip_for_path
    from meridian.relay_ops import delete_relay_hosts, remove_relay_nginx
    from meridian.ssh import ServerConnection

    relay = cluster.find_relay(relay_ip)
    if relay is None:
        raise ValueError(f"Relay {relay_ip} not found in cluster")

    # Delete host entries from panel (same function as imperative path)
    delete_relay_hosts(panel, relay)

    # Clean up nginx on exit node (best-effort)
    exit_node = cluster.find_node(relay.exit_node_ip)
    if exit_node:
        try:
            exit_conn = ServerConnection(exit_node.ip, exit_node.ssh_user, port=exit_node.ssh_port)
            remove_relay_nginx(exit_conn, relay)
        except (OSError, RuntimeError) as e:
            logger.warning("Could not clean up nginx for relay %s: %s", relay_ip, e)

    # Stop Realm service on relay host (same as imperative path)
    try:
        relay_conn = ServerConnection(relay_ip, relay.ssh_user, port=relay.ssh_port)
        relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
        relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
    except (OSError, RuntimeError) as e:
        logger.warning("Could not stop relay service on %s: %s", relay_ip, e)

    # Remove from cluster.yml
    cluster.relays = [r for r in cluster.relays if r.ip != relay_ip]
    cluster.save()

    # Clean local relay metadata
    from meridian.config import CREDS_BASE

    relay_file = CREDS_BASE / sanitize_ip_for_path(relay_ip) / "relay.yml"
    if relay_file.exists():
        relay_file.unlink()

    # Hybrid sync: drop from desired_relays too (only if managed declaratively).
    hybrid_sync_desired_relays_remove(cluster, relay_ip)


# ---------------------------------------------------------------------------
# Client operations
# ---------------------------------------------------------------------------


def add_client(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    name: str,
) -> Any:
    """Create a client in the panel.

    Hybrid sync: when ``cluster.desired_clients`` is non-None, the new name
    is also appended to that list and persisted, so a subsequent
    ``meridian apply`` does not see the user as drift and remove them.
    """
    squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
    user = panel.create_user(name, squad_uuids=squad_uuids)
    hybrid_sync_desired_clients_add(cluster, name)
    return user


def remove_client(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    *,
    name: str,
) -> bool:
    """Delete a client from the panel.

    Hybrid sync: when ``cluster.desired_clients`` is non-None, the name is
    also dropped from that list so subsequent plans do not propose
    re-creating the user.
    """
    user = panel.get_user(name)
    if user and user.uuid:
        deleted = panel.delete_user(user.uuid)
        if deleted:
            hybrid_sync_desired_clients_remove(cluster, name)
        return deleted
    # Even if the user did not exist on the panel, drop the desired entry —
    # otherwise the next plan would see drift and try to recreate them.
    hybrid_sync_desired_clients_remove(cluster, name)
    return False
