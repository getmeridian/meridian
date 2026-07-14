"""Node deployment and panel API helpers.

Extracted from ``panel_bootstrap.py`` — node container deployment,
host creation, panel readiness checks, and API token management.
No CLI interaction (prompts, Rich tables) — those stay in commands/.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Any

from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    ProtocolKey,
)
from meridian.config import REMNAWAVE_NODE_API_PORT
from meridian.core.errors import PanelSetupError
from meridian.remnawave import MeridianPanel, NodeCredentials, RemnawaveError
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)
_PUBLIC_PROXY_PORT = 443


@dataclass(frozen=True)
class _ManagedHost:
    remark: str
    address: str
    port: int
    config_profile_uuid: str
    inbound_uuid: str
    sni: str = ""
    host_header: str = ""
    path: str = ""
    alpn: str | None = None
    fingerprint: str | None = None
    security_layer: str = "DEFAULT"


# Panel API utilities
def panel_base_url(ip: str, domain: str, secret_path: str) -> str:
    """Build the panel base URL for API calls.

    Panel runs on 127.0.0.1:3000, reverse-proxied by nginx on the secret path.
    For first deploy before nginx is ready, we use SSH tunnel or direct access.
    Once nginx is up, we use https://<host>/<secret_path>.
    """
    host = domain or ip
    return f"https://{host}/{secret_path}/"


def create_api_token(base_url: str, auth_token: str) -> str:
    """Create a long-lived API token using the admin auth token.

    Remnawave's auth tokens (from login/register) are short-lived browser
    session tokens. API endpoints require a separate API token created via
    POST /api/tokens with the 'remnawave-client-type: browser' header.
    The API token is effectively permanent (~274 years).
    """
    import httpx

    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/tokens",
        json={"tokenName": "meridian-provisioner"},
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=False,
    )
    if resp.status_code not in (200, 201):
        raise PanelSetupError(
            f"Could not create API token ({resp.status_code}): {resp.text[:200]}",
            hint="The panel may need a fresh start",
        )
    data = resp.json()
    if isinstance(data, dict) and "response" in data:
        data = data["response"]
    token = data.get("token", "")
    if not token:
        raise PanelSetupError("API token creation succeeded but no token returned", category="bug")
    return token


def get_docker_gateway(conn: ServerConnection) -> str:
    """Get the Docker gateway IP for panel-to-node communication.

    When panel (bridge network) and node (host network) are on the same server,
    the panel cannot reach 127.0.0.1 on the host. It must use the gateway IP
    of its Docker network to reach services on the host network.

    We inspect the panel container's actual network, not the default bridge,
    because the panel runs on a custom 'remnawave-net' network.
    """
    result = conn.run(
        "docker inspect remnawave --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'",
        timeout=15,
    )
    gateway = result.stdout.strip() if result.returncode == 0 else ""
    return gateway or "172.17.0.1"


def check_panel_api_ready(base_url: str, retries: int = 20, delay: float = 3.0) -> bool:
    """Wait for the panel REST API to become reachable from the deployer.

    Uses poll_until_ready for centralized health polling.
    """
    import httpx

    from meridian.health import ReadinessTimeout, poll_until_ready

    def _check() -> bool:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/api/auth/login",
            timeout=10,
            verify=False,  # Self-signed cert during bootstrap
        )
        # Any response (even 405 Method Not Allowed) means the API is up
        return resp.status_code < 500

    try:
        poll_until_ready(
            _check,
            timeout=retries * delay,
            interval=delay,
            description="panel API",
        )
        return True
    except ReadinessTimeout:
        return False


# Panel API helpers
def inbound_protocol_key(tag: str) -> ProtocolKey | None:
    """Map a Remnawave inbound tag to Meridian's stable protocol key."""
    return {
        "vless-reality": ProtocolKey.REALITY,
        "vless-xhttp": ProtocolKey.XHTTP,
        "vless-wss": ProtocolKey.WSS,
        "hysteria2": ProtocolKey.HYSTERIA2,
    }.get(tag)


def select_default_squad_uuid(squads: list[Any]) -> str:
    """Select the Default-Squad UUID from a list of squads.

    Policy: prefer the squad named "Default-Squad"; fall back to the first
    available squad. Panel v2.7+ may not auto-create "Default-Squad".
    """
    for squad in squads:
        name = squad.get("name") if isinstance(squad, dict) else getattr(squad, "name", "")
        if name == "Default-Squad":
            uuid = squad.get("uuid") if isinstance(squad, dict) else getattr(squad, "uuid", "")
            return str(uuid or "")
    if squads:
        first = squads[0]
        uuid = first.get("uuid") if isinstance(first, dict) else getattr(first, "uuid", "")
        return str(uuid or "")
    return ""


def cache_inbounds(panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Fetch inbound definitions from the panel and cache their UUIDs."""
    try:
        inbounds = panel.list_inbounds()
        for ib in inbounds:
            key = inbound_protocol_key(ib.tag)
            if key:
                cluster.inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
        logger.info("Cached %d inbound references", len(cluster.inbounds))
    except RemnawaveError as e:
        raise PanelSetupError(
            f"Could not read required Remnawave inbounds: {e}",
            hint="No Hosts were changed. Check the panel and profile, then retry.",
        ) from e


def register_or_reuse_node(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    node_address: str,
    node_name: str,
) -> NodeCredentials:
    """Register a node or reuse an existing registration."""
    inbound_uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
    existing = panel.find_node_by_address(node_address)
    if existing:
        logger.info("Node at %s already registered, reconciling profile binding", node_address)
        panel.bind_node_profile(existing.uuid, cluster.config_profile_uuid, inbound_uuids)
        secret_key = panel.get_node_secret_key()
        if not secret_key:
            raise PanelSetupError(
                f"Remnawave returned no node secret for {node_name}",
                hint="The existing node binding was preserved; retry key generation before deploying the container.",
            )
        return NodeCredentials(uuid=existing.uuid, secret_key=secret_key)
    creds = panel.create_node(
        name=node_name,
        address=node_address,
        port=REMNAWAVE_NODE_API_PORT,
        config_profile_uuid=cluster.config_profile_uuid,
        inbound_uuids=inbound_uuids,
    )
    if not creds.uuid or not creds.secret_key:
        raise PanelSetupError(
            f"Remnawave returned incomplete credentials for node {node_name}",
            hint="The node container was not deployed. Inspect the panel node record, then retry.",
        )
    logger.info("Node registered: %s", node_name)
    return creds


def create_hosts_for_node(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    node_ip: str,
    domain: str,
    sni: str,
    reality_backend_port: int | None = None,
) -> None:
    """Converge direct Host entries to the node's public protocol listeners."""
    if reality_backend_port is not None:
        logger.debug(
            "Ignoring internal Reality backend port %s when publishing public Host",
            reality_backend_port,
        )
    node = cluster.find_node(node_ip)
    if node is None:
        raise PanelSetupError(
            f"Cannot publish Hosts for unknown node {node_ip}",
            hint="Persist the node before reconciling its Remnawave Hosts.",
            category="bug",
        )
    if not cluster.config_profile_uuid:
        raise PanelSetupError(
            "Cannot publish Hosts without a config profile",
            hint="Create and observe the Remnawave config profile before reconciling Hosts.",
            category="bug",
        )
    required_protocols = [ProtocolKey.REALITY, ProtocolKey.XHTTP]
    if domain:
        required_protocols.append(ProtocolKey.WSS)
    if node.hysteria2:
        required_protocols.append(ProtocolKey.HYSTERIA2)
    missing_protocols: list[str] = []
    for protocol in required_protocols:
        ref = cluster.get_inbound(protocol)
        if ref is None or not ref.uuid:
            missing_protocols.append(str(protocol))
    if missing_protocols:
        raise PanelSetupError(
            f"Config profile is missing required inbounds: {', '.join(missing_protocols)}",
            hint="Refresh the profile and inbound cache before publishing any Hosts.",
        )
    host_address = domain or node_ip
    try:
        existing_hosts = panel.list_hosts()
    except RemnawaveError as exc:
        raise PanelSetupError(
            f"Could not observe existing Remnawave Hosts: {exc}",
            hint="No Hosts were changed. Check panel connectivity, then retry.",
        ) from exc
    by_remark: dict[str, Any] = {}
    for host in existing_hosts:
        if host.remark in by_remark:
            raise PanelSetupError(
                f"Remnawave contains duplicate Hosts named '{host.remark}'",
                hint="Rename or remove the duplicate in Remnawave before retrying.",
                category="user",
            )
        by_remark[host.remark] = host

    desired: list[_ManagedHost] = []
    reality_ref = cluster.get_inbound(ProtocolKey.REALITY)
    if reality_ref and reality_ref.uuid:
        desired.append(
            _ManagedHost(
                remark=f"reality-{node_ip}",
                address=node_ip,
                port=_PUBLIC_PROXY_PORT,
                config_profile_uuid=cluster.config_profile_uuid,
                inbound_uuid=reality_ref.uuid,
                sni=sni,
                fingerprint="chrome",
            )
        )

    xhttp_ref = cluster.get_inbound(ProtocolKey.XHTTP)
    if xhttp_ref and xhttp_ref.uuid:
        desired.append(
            _ManagedHost(
                remark=f"xhttp-{host_address}",
                address=host_address,
                port=_PUBLIC_PROXY_PORT,
                config_profile_uuid=cluster.config_profile_uuid,
                inbound_uuid=xhttp_ref.uuid,
                sni=domain,
                host_header=domain,
                path=f"/{node.xhttp_path}" if node.xhttp_path else "",
                security_layer="TLS",
            )
        )

    if domain:
        wss_ref = cluster.get_inbound(ProtocolKey.WSS)
        if wss_ref and wss_ref.uuid:
            desired.append(
                _ManagedHost(
                    remark=f"wss-{domain}",
                    address=domain,
                    port=_PUBLIC_PROXY_PORT,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=wss_ref.uuid,
                    sni=domain,
                    host_header=domain,
                    path=f"/{node.ws_path}" if node.ws_path else "",
                    security_layer="TLS",
                )
            )

    hy2_ref = cluster.get_inbound(ProtocolKey.HYSTERIA2)
    if hy2_ref and hy2_ref.uuid:
        desired.append(
            _ManagedHost(
                remark=f"hysteria2-{host_address}",
                address=host_address,
                port=_PUBLIC_PROXY_PORT,
                config_profile_uuid=cluster.config_profile_uuid,
                inbound_uuid=hy2_ref.uuid,
                sni=domain,
                alpn="h3",
                security_layer="TLS",
            )
        )

    for spec in desired:
        _reconcile_host(panel, by_remark.get(spec.remark), spec)


def _reconcile_host(panel: MeridianPanel, existing: Any | None, desired: _ManagedHost) -> None:
    try:
        if existing is None:
            panel.create_host(
                remark=desired.remark,
                address=desired.address,
                port=desired.port,
                config_profile_uuid=desired.config_profile_uuid,
                inbound_uuid=desired.inbound_uuid,
                sni=desired.sni,
                host_header=desired.host_header,
                path=desired.path,
                alpn=desired.alpn,
                fingerprint=desired.fingerprint,
                security_layer=desired.security_layer,
                is_disabled=False,
            )
            logger.info("Host created: %s via %s:%s", desired.remark, desired.address, desired.port)
            return
        if _host_matches(existing, desired):
            logger.info("Host '%s' already converged", desired.remark)
            return
        if not existing.uuid:
            raise PanelSetupError(
                f"Cannot update Host '{desired.remark}' because Remnawave returned no UUID",
                category="bug",
            )
        panel.update_host(
            existing.uuid,
            remark=desired.remark,
            address=desired.address,
            port=desired.port,
            config_profile_uuid=desired.config_profile_uuid,
            inbound_uuid=desired.inbound_uuid,
            sni=desired.sni,
            host_header=desired.host_header,
            path=desired.path,
            alpn=desired.alpn,
            fingerprint=desired.fingerprint,
            security_layer=desired.security_layer,
            is_disabled=False,
        )
        logger.info("Host updated: %s", desired.remark)
    except RemnawaveError as exc:
        raise PanelSetupError(
            f"Could not reconcile required Host '{desired.remark}': {exc}",
            hint="The Host was not considered applied. Fix panel connectivity and retry.",
        ) from exc


def _host_matches(host: Any, desired: _ManagedHost) -> bool:
    expected = {
        "remark": desired.remark,
        "address": desired.address,
        "port": desired.port,
        "config_profile_uuid": desired.config_profile_uuid,
        "inbound_uuid": desired.inbound_uuid,
        "sni": desired.sni,
        "host": desired.host_header,
        "path": desired.path,
        "alpn": desired.alpn or "",
        "fingerprint": desired.fingerprint or "",
        "security_layer": desired.security_layer,
        "is_disabled": False,
    }
    return all(getattr(host, field, None) == value for field, value in expected.items())


def enforce_host_ordering(panel: MeridianPanel) -> None:
    """Reorder all hosts to enforce safest-first subscription ordering.

    Order: relay hosts before direct, Reality before XHTTP before WSS.
    This ensures subscription clients try the most censorship-resistant
    transport first regardless of creation order or panel UI reordering.
    """
    hosts = panel.list_hosts()
    if not hosts:
        return

    # Classify hosts by remark prefix
    def _sort_key(host: Any) -> tuple[int, int, str]:
        remark = host.remark.lower()
        # Relay hosts come first (lower priority number)
        is_relay = 0 if remark.startswith("relay-") else 1
        # Protocol ordering: reality=0, xhttp=1, wss=2, hysteria2=3, unknown=4
        if "reality" in remark:
            proto = 0
        elif "xhttp" in remark:
            proto = 1
        elif "wss" in remark:
            proto = 2
        elif "hysteria2" in remark:
            proto = 3
        else:
            proto = 4
        return (is_relay, proto, remark)

    sorted_hosts = sorted(hosts, key=_sort_key)
    ordered_uuids = [h.uuid for h in sorted_hosts if h.uuid]

    try:
        panel.reorder_hosts(ordered_uuids)
        logger.info("Host ordering enforced: %d hosts reordered", len(ordered_uuids))
    except RemnawaveError as e:
        logger.warning("Could not reorder hosts: %s", e)


def render_node_compose(image: str, node_api_port: int) -> str:
    """Render the Remnawave node Compose configuration."""
    return f"""\
# Remnawave Node - Xray Proxy Node
# Managed by Meridian. Manual edits will be overwritten on next run.
services:
  remnawave-node:
    image: {image}
    container_name: remnawave-node
    restart: always
    # Required for Remnawave node plugins and IP Control, which manage
    # nftables rules in the host network namespace.
    cap_add:
      - NET_ADMIN
    network_mode: host
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    env_file:
      - .env
    volumes:
      - ./logs:/var/log/remnawave
      - /etc/ssl/meridian:/etc/ssl/meridian:ro
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"
"""


def render_node_env(node_api_port: int, secret_key: str) -> str:
    """Render the Remnawave node environment file."""
    return f"""\
# Remnawave Node environment
# Managed by Meridian. Manual edits will be overwritten on next run.

NODE_PORT={node_api_port}
SECRET_KEY={secret_key}
"""


def deploy_node_container(
    conn: ServerConnection,
    secret_key: str,
    *,
    node_api_port: int | None = None,
    image: str | None = None,
) -> bool:
    """Deploy the Remnawave node container with the given secret key.

    Creates /opt/remnanode, writes docker-compose.yml and .env, pulls the
    image, and starts the container. Called from the post-provisioner phase
    after the panel API has registered the node and returned the mTLS secret.
    """
    from meridian.config import REMNAWAVE_NODE_API_PORT, REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
    from meridian.provision.containers import EnvFile, deploy_compose_stack

    node_dir = REMNAWAVE_NODE_DIR
    effective_port = REMNAWAVE_NODE_API_PORT if node_api_port is None else node_api_port
    effective_image = REMNAWAVE_NODE_IMAGE if image is None else image

    env_content = render_node_env(effective_port, secret_key)
    compose_content = render_node_compose(effective_image, effective_port)

    def _node_running() -> bool:
        check = conn.run(
            "docker inspect remnawave-node --format '{{.State.Running}}' 2>/dev/null",
            timeout=10,
        )
        return check.returncode == 0 and "true" in check.stdout.strip().lower()

    logger.info("Pulling Remnawave node image...")
    deploy_result = deploy_compose_stack(
        conn,
        node_dir,
        compose_content,
        env_files=[EnvFile(filename=".env", content=env_content, sensitive=True)],
        health_check=_node_running,
        health_timeout=30,
        health_interval=3.0,
        service_name="Remnawave node",
    )

    if deploy_result.changed:
        logger.info("Remnawave node deployed")
        node_healthy = True
    else:
        logger.warning("Node container issue: %s", deploy_result.detail)
        # Health timeout means the container might still be running
        # (deploy_result.logs is non-empty if we got past docker compose up)
        node_healthy = False

    # Allow Docker internal traffic to reach the node API port
    # (panel in bridge network → node on host via gateway IP)
    # Open UFW even on health timeout (container may still come up);
    # skip only on total deploy failure (mkdir/pull/compose-up).
    deploy_reached_containers = deploy_result.changed or "healthy" in deploy_result.detail
    if deploy_reached_containers:
        q_port = shlex.quote(str(effective_port))
        conn.run(
            f"ufw allow from 172.16.0.0/12 to any port {q_port} proto tcp"
            f" comment 'Meridian node API (Docker internal)' 2>/dev/null; true",
            timeout=15,
        )

    return node_healthy
