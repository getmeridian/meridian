"""Fleet inventory data model and builders for meridian-core."""

from __future__ import annotations

from typing import Literal, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from meridian.control.models import NodeRuntime
from meridian.core.models import CoreModel
from meridian.core.topology import ProtocolKind
from meridian.core.traffic import NodeTrafficEvidence

NodeRole = Literal["exit", "routing_gateway"]
ServerRole = Literal["panel", "exit", "relay", "routing_gateway"]
InventoryRole = Literal["panel+node", "node", "relay", "panel+routing_gateway", "routing_gateway"]
NodeStatus = Literal["connected", "failed", "disconnected", "disabled", "unknown"]
RelayHealth = Literal["healthy", "unhealthy", "unknown"]
SourceAvailability = Literal["available", "unavailable", "not_requested", "unknown"]
FleetHealth = Literal["healthy", "degraded", "unknown"]


class ApiNodeLike(Protocol):
    @property
    def uuid(self) -> str: ...

    @property
    def is_connected(self) -> bool: ...

    @property
    def is_disabled(self) -> bool: ...

    @property
    def xray_version(self) -> str: ...

    @property
    def traffic_used(self) -> int: ...


class ApiUserLike(Protocol):
    @property
    def username(self) -> str: ...

    @property
    def status(self) -> str: ...


class TopologyPanel(CoreModel):
    url: str
    display_url: str
    server_ip: str
    ssh_user: str
    ssh_port: int
    deployed_with: str


class TopologySubscriptionPage(CoreModel):
    enabled: bool
    path: str


class TopologyNode(CoreModel):
    ip: str
    name: str
    uuid: str
    is_panel_host: bool
    ssh_user: str
    ssh_port: int
    domain: str
    sni: str
    xhttp_path: str
    ws_path: str
    role: NodeRole = "exit"
    protocols: list[ProtocolKind] = Field(default_factory=list)


class RelayHostRef(CoreModel):
    protocol: str
    uuid: str


class TopologyRelay(CoreModel):
    ip: str
    name: str
    port: int
    ssh_user: str
    ssh_port: int
    exit_node_ip: str
    sni: str
    host_refs: list[RelayHostRef] = Field(default_factory=list)


class RelayHopMembership(CoreModel):
    chain: str
    position: int = Field(ge=1)
    advertised: bool
    health: RelayHealth = "unknown"


class TopologyServerMember(CoreModel):
    id: str
    ip: str
    name: str
    roles: list[ServerRole] = Field(default_factory=list)
    ssh_user: str
    ssh_port: int
    relay_hops: list[RelayHopMembership] = Field(default_factory=list)


class FleetTopology(CoreModel):
    panel: TopologyPanel
    subscription_page: TopologySubscriptionPage | None = None
    nodes: list[TopologyNode] = Field(default_factory=list)
    relays: list[TopologyRelay] = Field(default_factory=list)
    servers: list[TopologyServerMember] = Field(default_factory=list)
    access_users: list[str] | None = None

    @property
    def panel_url(self) -> str:
        return public_url(self.panel.display_url or self.panel.url)

    def find_node(self, ip: str) -> TopologyNode | None:
        return next((node for node in self.nodes if node.ip == ip), None)


class FleetSources(CoreModel):
    panel: SourceAvailability = "unknown"
    nodes: SourceAvailability = "unknown"
    users: SourceAvailability = "unknown"
    relays: SourceAvailability = "unknown"


class SubscriptionPageInventory(CoreModel):
    enabled: bool
    path: str


class ServerInventory(CoreModel):
    id: str
    ip: str
    name: str
    roles: list[ServerRole]
    ssh_user: str
    ssh_port: int
    relay_hops: list[RelayHopMembership] = Field(default_factory=list)


class PanelInventory(CoreModel):
    url: str
    server_ip: str
    ssh_user: str
    ssh_port: int
    healthy: bool
    deployed_with: str
    subscription_page: SubscriptionPageInventory


class NodeInventory(CoreModel):
    ip: str
    name: str
    uuid: str
    role: InventoryRole
    ssh_user: str
    ssh_port: int
    domain: str
    sni: str
    protocols: list[str]
    panel_status: NodeStatus
    xray_version: str


class RelayInventory(CoreModel):
    ip: str
    name: str
    role: InventoryRole
    port: int
    ssh_user: str
    ssh_port: int
    exit_node_ip: str
    exit_node_name: str
    sni: str
    host_refs: list[RelayHostRef] = Field(default_factory=list)
    host_count: int


class FleetInventorySummary(CoreModel):
    nodes: int
    relays: int

    @property
    def text(self) -> str:
        return f"{self.nodes} node(s), {self.relays} relay(s)"


class FleetInventory(CoreModel):
    panel: PanelInventory
    summary: FleetInventorySummary
    sources: FleetSources = Field(default_factory=FleetSources)
    servers: list[ServerInventory] = Field(default_factory=list)
    nodes: list[NodeInventory] = Field(default_factory=list)
    relays: list[RelayInventory] = Field(default_factory=list)


class PanelStatus(CoreModel):
    url: str
    healthy: bool


class FleetStatusNode(NodeTrafficEvidence):
    ip: str
    name: str
    uuid: str
    is_panel_host: bool
    role: NodeRole = "exit"
    status: NodeStatus
    xray_version: str
    traffic_bytes: int
    live: bool | None = None
    runtime: NodeRuntime | None = None
    runtime_available: bool | None = None
    in_sync: bool | None = None
    failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NodeListResult(CoreModel):
    """Result for listing configured nodes with live panel status."""

    nodes: list[FleetStatusNode] = Field(default_factory=list)


class RelayListEntry(CoreModel):
    """Configured relay metadata plus optional live host status."""

    ip: str
    name: str
    exit_node_ip: str
    port: int
    sni: str
    enabled: bool | None


class RelayListResult(CoreModel):
    """Result for listing configured relays."""

    relays: list[RelayListEntry] = Field(default_factory=list)


class FleetStatusRelay(CoreModel):
    ip: str
    name: str
    port: int
    exit_node_ip: str
    exit_node_name: str
    health: RelayHealth
    healthy: bool | None
    evidence: Literal["tcp_listener"] = "tcp_listener"


class FleetStatusSummary(CoreModel):
    health: FleetHealth
    needs_attention: bool
    nodes: int
    relays: int
    users: int
    active_users: int
    disabled_users: int
    other_users: int
    missing_access_users: int
    nonactive_access_users: int
    connected_nodes: int
    disconnected_nodes: int
    failed_nodes: int = 0
    out_of_sync_nodes: int = 0
    disabled_nodes: int
    unknown_nodes: int
    unhealthy_relays: int
    unknown_relays: int
    unknown_relay_hops: int = 0

    @property
    def text(self) -> str:
        return (
            f"{self.nodes} node(s), {self.relays} relay(s), "
            f"{self.active_users} active user(s), {self.missing_access_users} missing access user(s), "
            f"{self.out_of_sync_nodes} node(s) awaiting convergence, "
            f"{self.unhealthy_relays} unreachable relay listener(s), "
            f"{self.unknown_relays + self.unknown_relay_hops} unverified relay route(s)"
        )


class FleetStatus(CoreModel):
    panel: PanelStatus
    summary: FleetStatusSummary
    sources: FleetSources = Field(default_factory=FleetSources)
    servers: list[ServerInventory] = Field(default_factory=list)
    nodes: list[FleetStatusNode] = Field(default_factory=list)
    relays: list[FleetStatusRelay] = Field(default_factory=list)


def public_url(url: str) -> str:
    """Return a URL origin without secret paths, query strings, or fragments."""
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def node_api_status(api_node: ApiNodeLike | None) -> NodeStatus:
    if not api_node:
        return "unknown"
    connected = getattr(api_node, "is_connected", None)
    disabled = getattr(api_node, "is_disabled", None)
    if disabled is True:
        return "disabled"
    if getattr(api_node, "runtime_available", None) is False:
        return "unknown"
    if connected is True:
        # A control-service agent that polls but could not apply its document is no healthy node.
        runtime = getattr(api_node, "runtime", None)
        return (
            "failed"
            if getattr(api_node, "apply_failed", False) is True or (runtime and runtime.degraded)
            else "connected"
        )
    if connected is False or disabled is False:
        return "disconnected"
    return "unknown"


def _status_node(
    node: TopologyNode, api_node: ApiNodeLike | None, evidence: NodeTrafficEvidence | None = None
) -> FleetStatusNode:
    return FleetStatusNode(
        ip=node.ip,
        name=node.name,
        uuid=node.uuid,
        is_panel_host=node.is_panel_host,
        role=node.role,
        status=node_api_status(api_node),
        xray_version=api_node.xray_version if api_node else "",
        traffic_bytes=api_node.traffic_used if api_node else 0,
        live=api_node.is_connected if api_node else None,
        runtime=getattr(api_node, "runtime", None),
        runtime_available=getattr(api_node, "runtime_available", None),
        in_sync=getattr(api_node, "in_sync", None),
        failed=getattr(api_node, "failed", []),
        errors=getattr(api_node, "errors", []),
        **(evidence or NodeTrafficEvidence()).model_dump(),
    )


def build_node_list_result(
    nodes: Sequence[TopologyNode],
    api_nodes: Sequence[ApiNodeLike],
) -> NodeListResult:
    """Build a node list from configured topology and live panel observations."""
    api_by_uuid = {node.uuid: node for node in api_nodes}
    return NodeListResult(nodes=[_status_node(node, api_by_uuid.get(node.uuid)) for node in nodes])


def build_relay_list_result(
    relays: Sequence[TopologyRelay],
    enabled_by_endpoint: Mapping[tuple[str, int], bool | None],
) -> RelayListResult:
    """Build a relay list from configured topology and live host observations."""
    return RelayListResult(
        relays=[
            RelayListEntry(
                ip=relay.ip,
                name=relay.name,
                exit_node_ip=relay.exit_node_ip,
                port=relay.port,
                sni=relay.sni,
                enabled=enabled_by_endpoint.get((relay.ip, relay.port)),
            )
            for relay in relays
        ]
    )


def node_protocols(node: TopologyNode) -> list[str]:
    if node.protocols:
        return list(node.protocols)
    protocols = ["reality"]
    if node.xhttp_path:
        protocols.append("xhttp")
    if node.domain and node.ws_path:
        protocols.append("wss")
    return protocols


def build_server_inventory(topology: FleetTopology) -> list[ServerInventory]:
    """Build a role-oriented server list for graph/UI clients."""
    if topology.servers:
        return [
            ServerInventory(
                id=server.id,
                ip=server.ip,
                name=server.name,
                roles=server.roles,
                ssh_user=server.ssh_user,
                ssh_port=server.ssh_port,
                relay_hops=server.relay_hops,
            )
            for server in topology.servers
        ]

    by_ip: dict[str, ServerInventory] = {}

    def upsert(ip: str, name: str, roles: list[ServerRole], ssh_user: str, ssh_port: int) -> None:
        if not ip:
            return
        existing = by_ip.get(ip)
        if existing:
            merged_roles = list(dict.fromkeys([*existing.roles, *roles]))
            by_ip[ip] = existing.model_copy(update={"name": name or existing.name, "roles": merged_roles})
            return
        by_ip[ip] = ServerInventory(
            id=ip,
            ip=ip,
            name=name,
            roles=roles,
            ssh_user=ssh_user,
            ssh_port=ssh_port,
        )

    upsert(topology.panel.server_ip, "", ["panel"], topology.panel.ssh_user, topology.panel.ssh_port)
    for node in topology.nodes:
        roles: list[ServerRole] = [node.role]
        if node.is_panel_host:
            roles.insert(0, "panel")
        upsert(node.ip, node.name, roles, node.ssh_user, node.ssh_port)
    for relay in topology.relays:
        upsert(relay.ip, relay.name, ["relay"], relay.ssh_user, relay.ssh_port)
    return list(by_ip.values())


def build_fleet_status(
    topology: FleetTopology,
    *,
    panel_healthy: bool,
    api_nodes: Sequence[ApiNodeLike] | None = None,
    api_users: Sequence[ApiUserLike] | None = None,
    relay_health: Mapping[tuple[str, int], bool] | None = None,
    sources: FleetSources | None = None,
    node_evidence: Mapping[str, NodeTrafficEvidence] | None = None,
) -> FleetStatus:
    """Build fleet health status from local state plus live observations."""
    api_by_uuid = {node.uuid: node for node in api_nodes or []}
    relay_health = relay_health or {}
    sources = sources or FleetSources()

    nodes = []
    for node in topology.nodes:
        api_node = api_by_uuid.get(node.uuid)
        nodes.append(_status_node(node, api_node, (node_evidence or {}).get(node.uuid)))

    relays = []
    for relay in topology.relays:
        exit_node = topology.find_node(relay.exit_node_ip)
        observed = relay_health.get((relay.ip, relay.port))
        # A successful TCP connect proves only that the public listener accepts
        # connections. It cannot certify Realm's upstream or the encrypted path.
        relay_status: RelayHealth = "unhealthy" if observed is False else "unknown"
        relays.append(
            FleetStatusRelay(
                ip=relay.ip,
                name=relay.name,
                port=relay.port,
                exit_node_ip=relay.exit_node_ip,
                exit_node_name=exit_node.name if exit_node else "",
                health=relay_status,
                healthy=observed,
            )
        )

    observed_users = list(api_users or [])
    declared_users = set(topology.access_users or [])
    if topology.access_users is not None:
        observed_users = [user for user in observed_users if user.username in declared_users]
    observed_usernames = {user.username for user in observed_users}
    missing_access_users = len(declared_users - observed_usernames) if topology.access_users is not None else 0
    nonactive_access_users = (
        sum(1 for user in observed_users if user.status.upper() != "ACTIVE") if topology.access_users is not None else 0
    )
    user_statuses = [user.status for user in observed_users]
    active_users = sum(1 for status in user_statuses if status.upper() == "ACTIVE")
    disabled_users = sum(1 for status in user_statuses if status.upper() == "DISABLED")
    other_users = len(user_statuses) - active_users - disabled_users
    disconnected_nodes = sum(1 for node in nodes if node.status == "disconnected")
    failed_nodes = sum(1 for node in nodes if node.status == "failed")
    out_of_sync_nodes = sum(1 for node in nodes if node.runtime_available is True and node.in_sync is False)
    disabled_nodes = sum(1 for node in nodes if node.status == "disabled")
    unknown_nodes = sum(1 for node in nodes if node.status == "unknown")
    unhealthy_relays = sum(1 for relay in relays if relay.health == "unhealthy")
    unknown_relays = sum(1 for relay in relays if relay.health == "unknown")
    unknown_relay_hops = sum(
        1 for server in topology.servers for hop in server.relay_hops if not hop.advertised and hop.health == "unknown"
    )
    missing_required_sources = (
        sources.panel == "unavailable" or sources.nodes == "unavailable" or sources.users == "unavailable"
    )
    # Disabled, limited, or expired clients are operator or panel decisions, not faults.
    health: FleetHealth
    if (
        disconnected_nodes
        or failed_nodes
        or out_of_sync_nodes
        or disabled_nodes
        or unhealthy_relays
        or missing_access_users
    ):
        health = "degraded"
    elif (
        not panel_healthy
        or missing_required_sources
        or unknown_nodes > 0
        or unknown_relays > 0
        or unknown_relay_hops > 0
    ):
        health = "unknown"
    else:
        health = "healthy"
    needs_attention = health != "healthy"
    summary = FleetStatusSummary(
        health=health,
        needs_attention=needs_attention,
        nodes=len(nodes),
        relays=len(relays),
        users=len(user_statuses),
        active_users=active_users,
        disabled_users=disabled_users,
        other_users=other_users,
        missing_access_users=missing_access_users,
        nonactive_access_users=nonactive_access_users,
        connected_nodes=sum(1 for node in nodes if node.status == "connected"),
        disconnected_nodes=disconnected_nodes,
        failed_nodes=failed_nodes,
        out_of_sync_nodes=out_of_sync_nodes,
        disabled_nodes=disabled_nodes,
        unknown_nodes=unknown_nodes,
        unhealthy_relays=unhealthy_relays,
        unknown_relays=unknown_relays,
        unknown_relay_hops=unknown_relay_hops,
    )
    return FleetStatus(
        panel=PanelStatus(url=topology.panel_url, healthy=panel_healthy),
        summary=summary,
        sources=sources,
        servers=build_server_inventory(topology),
        nodes=nodes,
        relays=relays,
    )


def build_fleet_inventory(
    topology: FleetTopology,
    *,
    panel_healthy: bool,
    api_nodes: Sequence[ApiNodeLike] | None = None,
    sources: FleetSources | None = None,
) -> FleetInventory:
    """Build a redacted fleet inventory from local state plus panel status."""
    api_by_uuid = {node.uuid: node for node in api_nodes or []}
    sources = sources or FleetSources()

    panel = PanelInventory(
        url=topology.panel_url,
        server_ip=topology.panel.server_ip,
        ssh_user=topology.panel.ssh_user,
        ssh_port=topology.panel.ssh_port,
        healthy=panel_healthy,
        deployed_with=topology.panel.deployed_with,
        subscription_page=SubscriptionPageInventory(
            enabled=bool(topology.subscription_page and topology.subscription_page.enabled),
            path="",
        ),
    )

    nodes = []
    for node in topology.nodes:
        api_node = api_by_uuid.get(node.uuid)
        nodes.append(
            NodeInventory(
                ip=node.ip,
                name=node.name,
                uuid=node.uuid,
                role=(
                    "panel+routing_gateway"
                    if node.is_panel_host and node.role == "routing_gateway"
                    else "routing_gateway"
                    if node.role == "routing_gateway"
                    else "panel+node"
                    if node.is_panel_host
                    else "node"
                ),
                ssh_user=node.ssh_user,
                ssh_port=node.ssh_port,
                domain=node.domain,
                sni=node.sni,
                protocols=node_protocols(node),
                panel_status=node_api_status(api_node),
                xray_version=getattr(api_node, "xray_version", "") if api_node else "",
            )
        )

    relays = []
    for relay in topology.relays:
        exit_node = topology.find_node(relay.exit_node_ip)
        relays.append(
            RelayInventory(
                ip=relay.ip,
                name=relay.name,
                role="relay",
                port=relay.port,
                ssh_user=relay.ssh_user,
                ssh_port=relay.ssh_port,
                exit_node_ip=relay.exit_node_ip,
                exit_node_name=exit_node.name if exit_node else "",
                sni=relay.sni,
                host_refs=relay.host_refs,
                host_count=len(relay.host_refs),
            )
        )

    summary = FleetInventorySummary(
        nodes=len(topology.nodes),
        relays=len(topology.relays),
    )
    return FleetInventory(
        panel=panel,
        summary=summary,
        sources=sources,
        servers=build_server_inventory(topology),
        nodes=nodes,
        relays=relays,
    )
