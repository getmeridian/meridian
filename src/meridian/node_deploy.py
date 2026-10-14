"""Node container deployment and panel API helpers.

Remnawave node container rendering and rollout, panel readiness and node
handshake polling, and API token creation. No CLI interaction lives here.
"""

from __future__ import annotations

import ipaddress
import logging
import shlex

from meridian.config import REMNAWAVE_NODE_API_PORT
from meridian.core.errors import PanelSetupError
from meridian.host_runtime import NODE, NODE_DIR, NODE_PROJECT
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)
_API_TOKEN_EXPIRES_IN_DAYS = 100_000
# Neutral, so a server that later becomes a relay names nothing; retirement also
# deletes rules carrying the pre-rename comment.
NODE_API_RULE_COMMENT = "fw-node-api"
LEGACY_NODE_API_RULE_COMMENT = "Meridian node API (Docker internal)"


def delete_node_api_rules(conn: ServerConnection, *, legacy_only: bool = False) -> bool:
    """Delete the Docker-internal node API UFW rules (highest number first); True on success."""
    comments = (LEGACY_NODE_API_RULE_COMMENT,) if legacy_only else (NODE_API_RULE_COMMENT, LEGACY_NODE_API_RULE_COMMENT)
    patterns = " ".join(f"-e {shlex.quote('# ' + comment)}" for comment in comments)
    result = conn.run(
        f"ufw status numbered 2>/dev/null | grep -F {patterns}"
        r" | sed -n 's/^\[ *\([0-9][0-9]*\)\].*/\1/p'"
        ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null || exit 1; done',
        timeout=30,
    )
    return result.returncode == 0


# Panel API utilities
def panel_base_url(ip: str, domain: str, secret_path: str) -> str:
    """Build the panel base URL for API calls.

    Panel runs on 127.0.0.1:3000, reverse-proxied by nginx on the secret path.
    For first deploy before nginx is ready, we use SSH tunnel or direct access.
    Once nginx is up, we use https://<host>/<secret_path>.
    """
    host = domain or ip
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f"[{host}]"
    except ValueError:
        pass
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
        json={
            "name": "meridian-provisioner",
            "expiresInDays": _API_TOKEN_EXPIRES_IN_DAYS,
            "scopes": ["*"],
        },
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=True,
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
            verify=True,
        )
        # Any response (even 405 Method Not Allowed) means the API is up
        return resp.status_code < 500

    try:
        poll_until_ready(
            _check,
            timeout=retries * delay,
            interval=delay,
            description="panel API",
            transient=(httpx.HTTPError,),
        )
        return True
    except ReadinessTimeout:
        return False


def wait_for_node_connected(
    panel: MeridianPanel,
    node_uuid: str,
    *,
    timeout: float = 60,
    interval: float = 3.0,
) -> bool:
    """Wait until the panel has completed its asynchronous node handshake."""
    from meridian.health import ReadinessTimeout, poll_until_ready

    def _connected() -> bool:
        node = panel.get_node(node_uuid)
        return bool(node is not None and node.is_connected)

    try:
        poll_until_ready(
            _connected,
            timeout=timeout,
            interval=interval,
            description=f"Remnawave node {node_uuid}",
            transient=(RemnawaveError,),
        )
        return True
    except ReadinessTimeout:
        return False


def render_node_compose(image: str) -> str:
    """Render the Remnawave node Compose configuration under its neutral project and container name."""
    return f"""\
# Managed by Meridian. Manual edits will be overwritten on next run.
name: {NODE_PROJECT}
services:
  {NODE.name}:
    image: {image}
    container_name: {NODE.name}
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

    Creates ``NODE_DIR``, writes docker-compose.yml and .env, pulls the
    image, and starts the container. Called from the post-provisioner phase
    after the panel API has registered the node and returned the mTLS secret.
    """
    from meridian.config import REMNAWAVE_NODE_IMAGE
    from meridian.provision.containers import EnvFile, deploy_compose_stack

    node_dir = NODE_DIR
    effective_port = REMNAWAVE_NODE_API_PORT if node_api_port is None else node_api_port
    effective_image = REMNAWAVE_NODE_IMAGE if image is None else image

    env_content = render_node_env(effective_port, secret_key)
    compose_content = render_node_compose(effective_image)

    def _node_running() -> bool:
        check = conn.run(
            f"docker inspect {shlex.quote(NODE.name)} --format '{{{{.State.Running}}}}' 2>/dev/null",
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
        # UFW keeps an identical rule's old comment, so drop the pre-rename one first.
        delete_node_api_rules(conn, legacy_only=True)
        conn.run(
            f"ufw allow from 172.16.0.0/12 to any port {q_port} proto tcp"
            f" comment {shlex.quote(NODE_API_RULE_COMMENT)} 2>/dev/null; true",
            timeout=15,
        )

    return node_healthy
