"""Neutral names for what Meridian runs on its servers, and the Remnawave runtimes it removes.

Hosts can read ``ps``, ``systemctl``, and ``/opt``. Like the relay's ``netfwd``
units, nothing here names proxy software. The Docker names below are only read by
``meridian migrate`` and teardown, to remove a Remnawave fleet's containers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContainer:
    """One container: its neutral name (also the Compose service) and its pre-rename name."""

    name: str
    legacy: str


# A Remnawave control server: panel backend, PostgreSQL, Valkey, and the subscription page.
PANEL_PROJECT = "ctlsvc"
PANEL_DIR = f"/opt/{PANEL_PROJECT}"
LEGACY_PANEL_DIR = "/opt/remnawave"
PANEL_DATABASE = RuntimeContainer(f"{PANEL_PROJECT}-db", "remnawave-db")
# Dependents first, so a stop never pulls the database from under a running backend.
PANEL_STOP_ORDER = (
    RuntimeContainer(f"{PANEL_PROJECT}-web", "remnawave-subscription-page"),
    RuntimeContainer(f"{PANEL_PROJECT}-api", "remnawave"),
    RuntimeContainer(f"{PANEL_PROJECT}-cache", "remnawave-redis"),
    PANEL_DATABASE,
)
# The unpinned project took the directory's name; neither object holds data.
LEGACY_PANEL_NETWORK = "remnawave_remnawave-net"
LEGACY_PANEL_SOCKET_VOLUME = "remnawave_valkey-socket"

# A Remnawave exit's node container.
NODE_DIR = "/opt/netagent"
LEGACY_NODE_DIR = "/opt/remnanode"
NODE = RuntimeContainer("netagent", "remnawave-node")


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
