"""Node deployment and panel API helpers.

Extracted from ``panel_bootstrap.py`` — node container deployment,
host creation, panel readiness checks, and API token management.
No CLI interaction (prompts, Rich tables) — those stay in commands/.
"""

from __future__ import annotations

import logging
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


# Backward-compatible alias for tests
wait_for_panel_api = check_panel_api_ready


# Panel API helpers
def select_default_squad_uuid(squads: list[dict[str, Any]]) -> str:
    """Select the Default-Squad UUID from a list of squads.

    Policy: prefer the squad named "Default-Squad"; fall back to the first
    available squad. Panel v2.7+ may not auto-create "Default-Squad".
    """
    for s in squads:
        if isinstance(s, dict) and s.get("name") == "Default-Squad":
            return str(s.get("uuid", ""))
    if squads and isinstance(squads[0], dict):
        return str(squads[0].get("uuid", ""))
    return ""


def cache_inbounds(panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Fetch inbound definitions from the panel and cache their UUIDs."""
    try:
        inbounds = panel.list_inbounds()
        tag_map = {
            "vless-reality": ProtocolKey.REALITY,
            "vless-xhttp": ProtocolKey.XHTTP,
            "vless-wss": ProtocolKey.WSS,
        }
        for ib in inbounds:
            key = tag_map.get(ib.tag)
            if key:
                cluster.inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
        logger.info("Cached %d inbound references", len(cluster.inbounds))
    except RemnawaveError as e:
        logger.warning("Could not cache inbound references: %s", e)


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
        logger.info("Node at %s already registered, reusing", node_address)
        secret_key = panel.get_node_secret_key()
        return NodeCredentials(uuid=existing.uuid, secret_key=secret_key)
    creds = panel.create_node(
        name=node_name,
        address=node_address,
        port=REMNAWAVE_NODE_API_PORT,
        config_profile_uuid=cluster.config_profile_uuid,
        inbound_uuids=inbound_uuids,
    )
    logger.info("Node registered: %s", node_name)
    return creds


def create_hosts_for_node(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    node_ip: str,
    domain: str,
    sni: str,
    reality_port: int,
) -> None:
    """Create direct host entries for a node's protocols."""
    host_address = domain or node_ip

    # Batch-fetch all hosts once instead of per-protocol find_host_by_remark
    try:
        existing_remarks = {h.remark for h in panel.list_hosts()}
    except RemnawaveError:
        existing_remarks = set()

    # Reality host (direct IP, port 443 or computed)
    reality_ref = cluster.get_inbound(ProtocolKey.REALITY)
    if reality_ref and reality_ref.uuid:
        remark = f"reality-{node_ip}"
        if remark in existing_remarks:
            logger.info("Host '%s' already exists, skipping", remark)
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=node_ip,
                    port=reality_port,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=reality_ref.uuid,
                    sni=sni,
                    fingerprint="chrome",
                    security_layer="DEFAULT",
                )
                logger.info("Host created: Reality via %s:%s", node_ip, reality_port)
            except RemnawaveError as e:
                logger.warning("Could not create Reality host: %s", e)

    # XHTTP host (via domain or IP, port 443 through nginx)
    xhttp_ref = cluster.get_inbound(ProtocolKey.XHTTP)
    if xhttp_ref and xhttp_ref.uuid:
        remark = f"xhttp-{host_address}"
        if remark in existing_remarks:
            logger.info("Host '%s' already exists, skipping", remark)
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=host_address,
                    port=443,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=xhttp_ref.uuid,
                    security_layer="TLS",
                )
                logger.info("Host created: XHTTP via %s:443", host_address)
            except RemnawaveError as e:
                logger.warning("Could not create XHTTP host: %s", e)

    # WSS host (domain mode only, port 443 through CDN)
    if domain:
        wss_ref = cluster.get_inbound(ProtocolKey.WSS)
        if wss_ref and wss_ref.uuid:
            remark = f"wss-{domain}"
            if remark in existing_remarks:
                logger.info("Host '%s' already exists, skipping", remark)
            else:
                try:
                    panel.create_host(
                        remark=remark,
                        address=domain,
                        port=443,
                        config_profile_uuid=cluster.config_profile_uuid,
                        inbound_uuid=wss_ref.uuid,
                        security_layer="TLS",
                    )
                    logger.info("Host created: WSS via %s:443", domain)
                except RemnawaveError as e:
                    logger.warning("Could not create WSS host: %s", e)


def deploy_node_container(conn: ServerConnection, secret_key: str) -> bool:
    """Deploy the Remnawave node container with the given secret key.

    Creates /opt/remnanode, writes docker-compose.yml and .env, pulls the
    image, and starts the container. Called from the post-provisioner phase
    after the panel API has registered the node and returned the mTLS secret.
    """
    from meridian.config import REMNAWAVE_NODE_API_PORT, REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
    from meridian.provision.containers import EnvFile, deploy_compose_stack
    from meridian.provision.remnawave_node import render_node_compose, render_node_env

    node_dir = REMNAWAVE_NODE_DIR

    env_content = render_node_env(REMNAWAVE_NODE_API_PORT, secret_key)
    compose_content = render_node_compose(REMNAWAVE_NODE_IMAGE, REMNAWAVE_NODE_API_PORT)

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
        conn.run(
            f"ufw allow from 172.16.0.0/12 to any port {REMNAWAVE_NODE_API_PORT} proto tcp"
            f" comment 'Meridian node API (Docker internal)' 2>/dev/null; true",
            timeout=15,
        )

    return node_healthy


# Re-export deploy_client_page for backward compatibility (moved to pwa.py)
from meridian.pwa import deploy_client_page  # noqa: F401, E402
