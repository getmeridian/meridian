"""Neutral names for the Docker runtimes on exits and the control server.

Hosts can read ``ps``, ``docker ps``, and ``/opt``. Like the relay's ``netfwd``
units, nothing here names proxy software; the images keep their upstream tags.
The ``legacy`` names are only read to migrate and clean up.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContainer:
    """One container: its neutral name (also the Compose service), pre-rename name, and role."""

    name: str
    legacy: str
    role: str

    @property
    def label(self) -> str:
        """What check and doctor display: the role first, the name to type second."""
        return f"{self.role} ({self.name})"


# Control server: panel backend, PostgreSQL, Valkey, and the subscription page.
PANEL_PROJECT = "ctlsvc"
PANEL_DIR = f"/opt/{PANEL_PROJECT}"
LEGACY_PANEL_DIR = "/opt/remnawave"
PANEL_BACKEND = RuntimeContainer(f"{PANEL_PROJECT}-api", "remnawave", "Panel backend")
PANEL_DATABASE = RuntimeContainer(f"{PANEL_PROJECT}-db", "remnawave-db", "Panel database")
PANEL_CACHE = RuntimeContainer(f"{PANEL_PROJECT}-cache", "remnawave-redis", "Panel cache")
SUBSCRIPTION_PAGE = RuntimeContainer(f"{PANEL_PROJECT}-web", "remnawave-subscription-page", "Subscription page")
PANEL_CORE = (PANEL_BACKEND, PANEL_DATABASE, PANEL_CACHE)
# Dependents first, so a stop never pulls the database from under a running backend.
PANEL_STOP_ORDER = (SUBSCRIPTION_PAGE, PANEL_BACKEND, PANEL_CACHE, PANEL_DATABASE)
# The unpinned project took the directory's name; neither object holds data (Valkey keeps
# only its socket there, and PostgreSQL's data is the ``data/`` bind mount in the directory).
LEGACY_PANEL_NETWORK = "remnawave_remnawave-net"
LEGACY_PANEL_SOCKET_VOLUME = "remnawave_valkey-socket"

# Exits and routing gateways: the panel-managed node agent (Xray).
NODE_PROJECT = "netagent"
NODE_DIR = f"/opt/{NODE_PROJECT}"
LEGACY_NODE_DIR = "/opt/remnanode"
NODE = RuntimeContainer(NODE_PROJECT, "remnawave-node", "Exit node")

CONTAINERS = (*PANEL_STOP_ORDER, NODE)


def container_role(name: str) -> str:
    """Role of a container name, current or pre-rename; empty for containers Meridian does not run."""
    for container in CONTAINERS:
        if name == container.name:
            return container.role
        if name == container.legacy:
            return f"{container.role}, pre-rename (meridian apply renames it)"
    return ""


# Meridian's own control service (`meridian serve`, a uv-managed tool) and node agent.
# The agent's names stay bland like the relay's: the Xray it runs is `netcore`.
CONTROL_SERVICE_NAME = "ctld"
CONTROL_SERVICE_UNIT = f"{CONTROL_SERVICE_NAME}.service"
CONTROL_SERVICE_DIR = f"/opt/{CONTROL_SERVICE_NAME}"
CONTROL_SERVICE_CONFIG = f"/etc/{CONTROL_SERVICE_NAME}/config.json"
CONTROL_SERVICE_STATE_DIR = f"/var/lib/{CONTROL_SERVICE_NAME}"
AGENT_NAME = "netagent"
AGENT_BINARY = f"/usr/local/bin/{AGENT_NAME}"
AGENT_UNIT = f"{AGENT_NAME}.service"
AGENT_CONFIG_DIR = f"/etc/{AGENT_NAME}"
AGENT_BOOTSTRAP = f"{AGENT_CONFIG_DIR}/bootstrap.json"
AGENT_STATE_DIR = f"/var/lib/{AGENT_NAME}"
AGENT_LIB_DIR = f"/usr/local/lib/{AGENT_NAME}"
CORE_BINARY = f"{AGENT_LIB_DIR}/netcore"
CORE_CONFIG = f"{AGENT_CONFIG_DIR}/core.json"
CORE_UNIT = "netcore.service"
