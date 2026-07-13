"""Relay infrastructure operations — shared by commands and reconciler.

Business logic for relay setup, teardown, and host management.
No CLI interaction (prompts, Rich tables) — those stay in commands/relay.py.
"""

from __future__ import annotations

import logging
import re
import shlex

from meridian.cluster import ClusterConfig, ProtocolKey, RelayEntry
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relay label / port helpers
# ---------------------------------------------------------------------------


def relay_label(relay: RelayEntry) -> str:
    """Derive a filesystem/remark-safe label from a relay entry."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", relay.name or relay.ip)


def relay_xray_port(relay_ip: str) -> int:
    """Deterministic Xray port for a relay inbound (range 40000-49999)."""
    from meridian.core.deploy_planning import compute_relay_port

    return compute_relay_port(relay_ip)


# ---------------------------------------------------------------------------
# Exit node resolution
# ---------------------------------------------------------------------------


def find_exit_node(cluster: ClusterConfig, exit_arg: str) -> str:
    """Resolve --exit flag to a node IP. Accepts IP or node name.

    Raises ``ValueError`` when the exit node cannot be resolved.
    """
    node = cluster.find_node(exit_arg)
    if node is not None:
        return node.ip
    if not exit_arg and len(cluster.nodes) == 1:
        return cluster.nodes[0].ip
    if not exit_arg:
        raise ValueError("Multiple nodes in cluster -- specify which one with --exit")
    raise ValueError(f"Exit node '{exit_arg}' not found in cluster")


# ---------------------------------------------------------------------------
# Nginx SNI routing on exit server
# ---------------------------------------------------------------------------


def deploy_relay_nginx(
    exit_conn: ServerConnection,
    relay_sni: str,
    relay_ip: str,
    relay_name: str = "",
) -> bool:
    """Create per-relay nginx SNI map + upstream on exit server and reload."""
    label = relay_label(RelayEntry(ip=relay_ip, name=relay_name))
    port = relay_xray_port(relay_ip)
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
        logger.warning("could not create relay nginx map directory: %s", mkdir.stderr.strip() or mkdir.stdout.strip())
        return False
    map_write = exit_conn.put_text(
        f"/etc/nginx/stream.d/relay-maps/{label}.conf",
        f"    {relay_sni}  {upstream};\n",
        mode="644",
        timeout=15,
        operation_name="write relay nginx map",
    )
    if map_write.returncode != 0:
        logger.warning("could not write relay nginx map: %s", map_write.stderr.strip() or map_write.stdout.strip())
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
        logger.warning(
            "could not write relay nginx upstream: %s", upstream_write.stderr.strip() or upstream_write.stdout.strip()
        )
        return False
    result = exit_conn.run("nginx -t 2>&1", timeout=15)
    if result.returncode != 0:
        logger.warning("nginx config validation failed: %s", result.stderr.strip() or result.stdout.strip())
        return False
    reload_result = exit_conn.run("systemctl reload nginx", timeout=15)
    if reload_result.returncode != 0:
        logger.warning("nginx reload failed: %s", reload_result.stderr.strip() or reload_result.stdout.strip())
        return False
    logger.info("nginx updated: SNI=%s -> port %d", relay_sni, port)
    return True


def remove_relay_nginx(exit_conn: ServerConnection, relay: RelayEntry) -> bool:
    """Remove per-relay nginx config files from the exit server and reload."""
    q = shlex.quote(relay_label(relay))
    exit_conn.run(
        f"rm -f /etc/nginx/stream.d/relay-maps/{q}.conf /etc/nginx/stream.d/meridian-relay-{q}.conf",
        timeout=15,
    )
    if exit_conn.run("nginx -t 2>&1", timeout=15).returncode != 0:
        logger.warning("nginx config validation failed after relay removal")
        return False
    if exit_conn.run("systemctl reload nginx", timeout=15).returncode != 0:
        logger.warning("nginx reload failed after relay removal")
        return False
    return True


# ---------------------------------------------------------------------------
# Remnawave Host entries for relays
# ---------------------------------------------------------------------------


def create_relay_hosts(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    relay_ip: str,
    relay_port: int,
    relay_sni: str,
    relay_name: str,
) -> dict[str, str]:
    """Create Remnawave Host entries for a relay. Returns {protocol_key: host_uuid}."""
    host_uuids: dict[str, str] = {}
    label = relay_label(RelayEntry(ip=relay_ip, name=relay_name))

    # Panel v2.7+ only accepts DEFAULT/TLS/NONE for securityLayer.
    # Reality hosts use "DEFAULT" (panel infers reality from inbound type).
    # WSS is excluded: CDN routing (Cloudflare) already provides geographic
    # flexibility and L4 TCP relaying does not help traffic that routes
    # through the CDN anyway.
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
            logger.info("Host '%s' already exists, reusing", remark)
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
            logger.info("Host created: %s", remark)
        except RemnawaveError as e:
            logger.warning("Could not create %s host: %s", proto_key, e)
    return host_uuids


def delete_relay_hosts(panel: MeridianPanel, relay: RelayEntry) -> None:
    """Delete all Remnawave Host entries for a relay."""
    for proto_key, host_uuid in relay.host_uuids.items():
        if not host_uuid:
            continue
        try:
            panel.delete_host(host_uuid)
            logger.info("Host deleted: %s (%s...)", proto_key, host_uuid[:8])
        except RemnawaveError as e:
            logger.warning("Could not delete %s host %s...: %s", proto_key, host_uuid[:8], e)
