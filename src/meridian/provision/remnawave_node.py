"""Remnawave node provisioning step.

Deploys the Remnawave node container on a proxy server. The node uses
host networking so Xray can bind to specific localhost ports. The node
API port is used for panel->node communication (mTLS, token-authenticated).
"""

from __future__ import annotations

from meridian.config import (
    REMNAWAVE_NODE_API_PORT,
    REMNAWAVE_NODE_DIR,
    REMNAWAVE_NODE_IMAGE,
)
from meridian.facts import ServerFacts
from meridian.provision.containers import deploy_compose_stack
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

# Container name used for the node
_NODE_CONTAINER = "remnawave-node"


def _render_node_compose(image: str, node_api_port: int) -> str:
    """Render the docker-compose.yml for the Remnawave node."""
    return f"""\
# Remnawave Node - Xray Proxy Node
# Managed by Meridian. Manual edits will be overwritten on next run.
services:
  remnawave-node:
    image: {image}
    container_name: {_NODE_CONTAINER}
    restart: always
    # NET_ADMIN is MANDATORY per upstream docs (panel 2.6.2+, 2.7.0+). It
    # enables the node plugin system (Torrent Blocker, Ingress/Egress Filter,
    # Connection Drop) and the IP Control panel feature — all of which push
    # nftables rules into the host network namespace. Without NET_ADMIN the
    # kernel rejects those syscalls with EPERM and the panel UI silently
    # reports nothing. Reality/XHTTP/WSS proxying does not need it, but
    # since the panel lets the operator enable those features without any
    # Meridian-CLI involvement, dropping the capability is a UX regression
    # waiting to happen.
    cap_add:
      - NET_ADMIN
    # Host networking required so Xray can bind to specific ports
    network_mode: host
    env_file:
      - .env
    volumes:
      - ./logs:/var/log/remnawave
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"
"""


def _render_node_env(node_api_port: int, secret_key: str) -> str:
    """Render the .env file for the Remnawave node."""
    return f"""\
# Remnawave Node environment
# Managed by Meridian. Manual edits will be overwritten on next run.

NODE_PORT={node_api_port}
SECRET_KEY={secret_key}
"""


class DeployRemnawaveNode:
    """Deploy Remnawave node container on a proxy server.

    The node uses ``network_mode: host`` so Xray can bind to specific
    localhost ports. The SECRET_KEY (base64 JSON with mTLS certs) must
    be provided via the provision context (``ctx.get("node_secret_key")``)
    by a prior step that calls the panel API to register the node.

    Idempotency: skipped when the container is already running.
    """

    name = "Deploy Remnawave node"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        node_dir = REMNAWAVE_NODE_DIR
        node_api_port = REMNAWAVE_NODE_API_PORT
        image = REMNAWAVE_NODE_IMAGE

        # -- Retrieve SECRET_KEY from provision context --
        secret_key: str | None = ctx.get("node_secret_key")
        if not secret_key:
            return StepResult(
                name=self.name,
                status="failed",
                detail=(
                    "node_secret_key not found in provision context — "
                    "a prior step must call the panel API to register the node "
                    "and store the mTLS certificate bundle as ctx['node_secret_key']"
                ),
            )

        # -- Idempotency check: is the container already running? --
        if ServerFacts(conn).container_state(_NODE_CONTAINER).running:
            return StepResult(
                name=self.name,
                status="skipped",
                detail="container already running",
            )

        # -- Build compose and env content --
        compose_content = _render_node_compose(image=image, node_api_port=node_api_port)
        env_content = _render_node_env(node_api_port=node_api_port, secret_key=secret_key)

        # -- Health check: poll the node API port --
        def _health_check() -> bool:
            result = conn.run(
                f"ss -tlnp 'sport = :{node_api_port}' | grep -q ':{node_api_port}'",
                timeout=15,
            )
            return result.returncode == 0

        # -- Deploy via shared compose lifecycle --
        deploy_result = deploy_compose_stack(
            conn,
            node_dir,
            compose_content,
            env_content=env_content,
            dirs=[f"{node_dir}/logs"],
            health_check=_health_check,
            health_timeout=60,
            health_interval=3,
            pull_retries=3,
            pull_retry_delay=10,
            pull_timeout=300,
            service_name="remnawave-node",
        )

        if not deploy_result.changed:
            return StepResult(
                name=self.name,
                status="failed",
                detail=deploy_result.detail,
            )

        return StepResult(name=self.name, status="changed")
