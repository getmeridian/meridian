"""ClusterConfig adapter for meridian-core topology models."""

from __future__ import annotations

from collections.abc import Sequence

from meridian.cluster import ClusterConfig, NodeEntry, RelayEntry, WorkloadBinding
from meridian.core.errors import LocalStateError
from meridian.core.fleet import (
    DesiredNodeSpec,
    DesiredRelaySpec,
    FleetTopology,
    RelayHopMembership,
    RelayHostRef,
    TopologyNode,
    TopologyPanel,
    TopologyRelay,
    TopologyServerMember,
    TopologySubscriptionPage,
)
from meridian.core.topology import ProtocolKind
from meridian.servers import ServerEntry, ServerRegistry


def topology_from_local_cluster(cluster: ClusterConfig) -> FleetTopology:
    """Convert cluster state, resolving V4 server references from local state."""
    from meridian.config import SERVER_PROFILES_FILE

    servers = ServerRegistry(SERVER_PROFILES_FILE).list() if cluster.topology_intent is not None else None
    return topology_from_cluster(cluster, servers=servers)


def topology_from_cluster(
    cluster: ClusterConfig,
    *,
    servers: Sequence[ServerEntry] | None = None,
) -> FleetTopology:
    """Convert the current YAML-backed cluster model into core topology.

    V4 intent stores stable server references instead of duplicating addresses
    in ``cluster.yml``. Callers that project V4 state therefore provide the
    saved server registry (or use :func:`topology_from_local_cluster`).
    """
    nodes = cluster.nodes
    relays = cluster.relays
    panel_server_ip = cluster.panel.server_ip
    panel_ssh_user = cluster.panel.ssh_user
    panel_ssh_port = cluster.panel.ssh_port
    if cluster.topology_intent is not None:
        if servers is None:
            raise LocalStateError(
                "Saved server profiles are required to read the V4 topology.",
                hint="Restore ~/.meridian/servers.json, then retry.",
            )
        server_map = _v4_server_map(cluster, servers)
        nodes = _v4_nodes(cluster, server_map)
        relays = _v4_relays(cluster, server_map)
        control = server_map[cluster.topology_intent.control.server_ref]
        if not panel_server_ip:
            panel_server_ip = control.host
            panel_ssh_user = control.user
            panel_ssh_port = control.port

    return FleetTopology(
        panel=TopologyPanel(
            url=cluster.panel.url,
            display_url=cluster.panel.display_url,
            server_ip=panel_server_ip,
            ssh_user=panel_ssh_user,
            ssh_port=panel_ssh_port,
            deployed_with=cluster.panel.deployed_with,
        ),
        subscription_page=(
            TopologySubscriptionPage(
                enabled=cluster.subscription_page.enabled,
                path=cluster.subscription_page.path,
            )
            if cluster.subscription_page
            else None
        ),
        nodes=[
            TopologyNode(
                ip=node.ip,
                name=node.name,
                uuid=node.uuid,
                is_panel_host=node.is_panel_host,
                ssh_user=node.ssh_user,
                ssh_port=node.ssh_port,
                domain=node.domain,
                sni=node.sni,
                xhttp_path=node.xhttp_path,
                ws_path=node.ws_path,
                role=(
                    "routing_gateway"
                    if cluster.topology_intent is not None
                    and node.name in {gateway.id for gateway in cluster.topology_intent.routing_gateways}
                    else "exit"
                ),
                protocols=_projected_protocols(
                    node,
                    assume_reality=cluster.topology_intent is None,
                ),
            )
            for node in nodes
        ],
        relays=[
            TopologyRelay(
                ip=relay.ip,
                name=relay.name,
                port=relay.port,
                ssh_user=relay.ssh_user,
                ssh_port=relay.ssh_port,
                exit_node_ip=relay.exit_node_ip,
                sni=relay.sni,
                host_refs=[
                    RelayHostRef(protocol=str(protocol), uuid=uuid)
                    for protocol, uuid in sorted(relay.host_uuids.items())
                ],
            )
            for relay in relays
        ],
        servers=_v4_members(cluster, server_map) if cluster.topology_intent is not None else [],
        access_users=(list(cluster.topology_intent.access.users) if cluster.topology_intent is not None else None),
        desired_nodes=(
            [
                DesiredNodeSpec(
                    host=desired.host,
                    name=desired.name,
                    ssh_user=desired.ssh_user,
                    ssh_port=desired.ssh_port,
                    domain=desired.domain,
                    sni=desired.sni,
                    warp=desired.warp,
                )
                for desired in cluster.desired_nodes
            ]
            if cluster.desired_nodes is not None
            else None
        ),
        desired_relays=(
            [
                DesiredRelaySpec(
                    host=desired.host,
                    name=desired.name,
                    ssh_user=desired.ssh_user,
                    ssh_port=desired.ssh_port,
                    exit_node=desired.exit_node,
                    sni=desired.sni,
                )
                for desired in cluster.desired_relays
            ]
            if cluster.desired_relays is not None
            else None
        ),
    )


def _projected_protocols(node: NodeEntry, *, assume_reality: bool) -> list[ProtocolKind]:
    protocols: list[ProtocolKind] = []
    if assume_reality or node.reality_public_key or node.sni:
        protocols.append("reality")
    if node.xhttp_path:
        protocols.append("xhttp")
    if node.domain and node.ws_path:
        protocols.append("wss")
    if node.hysteria2:
        protocols.append("hysteria2")
    return protocols


def _v4_server_map(
    cluster: ClusterConfig,
    servers: Sequence[ServerEntry],
) -> dict[str, ServerEntry]:
    intent = cluster.topology_intent
    if intent is None:
        return {}
    by_id = {server.id: server for server in servers if server.id}
    required = {
        intent.control.server_ref,
        *(exit_.server_ref for exit_ in intent.exits),
        *(server_ref for relay in intent.transparent_relays for server_ref in relay.hop_server_refs),
        *(gateway.server_ref for gateway in intent.routing_gateways),
    }
    missing = sorted(required - set(by_id))
    if missing:
        raise LocalStateError(
            "V4 topology references missing saved servers: " + ", ".join(missing) + ".",
            hint="Restore the saved server profiles or repair the topology with meridian setup.",
        )
    return by_id


def _active_workload(cluster: ClusterConfig, workload_id: str) -> WorkloadBinding | None:
    return next(
        (
            workload
            for workload in sorted(cluster.workloads, key=lambda item: item.generation, reverse=True)
            if workload.id == workload_id and workload.active
        ),
        None,
    )


def _v4_nodes(
    cluster: ClusterConfig,
    servers: dict[str, ServerEntry],
) -> list[NodeEntry]:
    intent = cluster.topology_intent
    if intent is None:
        return []
    nodes: list[NodeEntry] = []
    for exit_ in intent.exits:
        server = servers[exit_.server_ref]
        workload = _active_workload(cluster, exit_.id)
        reality = next((path for path in exit_.paths if path.protocol == "reality"), None)
        xhttp = next((path for path in exit_.paths if path.protocol == "xhttp"), None)
        wss = next((path for path in exit_.paths if path.protocol == "wss"), None)
        tls_path = next((path for path in exit_.paths if path.tls_sni), None)
        nodes.append(
            NodeEntry(
                ip=server.host,
                uuid=workload.node_uuids.get(exit_.server_ref, "") if workload else "",
                name=exit_.id,
                ssh_user=server.user,
                ssh_port=server.port,
                sni=reality.reality_sni if reality else "",
                domain=tls_path.tls_sni if tls_path else "",
                is_panel_host=exit_.server_ref == intent.control.server_ref,
                xhttp_path=xhttp.path if xhttp else "",
                ws_path=wss.path if wss else "",
                warp=exit_.warp,
                hysteria2=any(path.protocol == "hysteria2" for path in exit_.paths),
            )
        )
    paths_by_id = {path.id: path for exit_ in intent.exits for path in exit_.paths}
    for gateway in intent.routing_gateways:
        server = servers[gateway.server_ref]
        workload = _active_workload(cluster, gateway.id)
        bridge_path = paths_by_id[gateway.bridge_path_ref]
        nodes.append(
            NodeEntry(
                ip=server.host,
                uuid=workload.node_uuids.get(gateway.server_ref, "") if workload else "",
                name=gateway.id,
                ssh_user=server.user,
                ssh_port=server.port,
                sni=bridge_path.reality_sni,
                is_panel_host=gateway.server_ref == intent.control.server_ref,
                hysteria2=False,
            )
        )
    return nodes


def _v4_relays(
    cluster: ClusterConfig,
    servers: dict[str, ServerEntry],
) -> list[RelayEntry]:
    intent = cluster.topology_intent
    if intent is None:
        return []
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    relays: list[RelayEntry] = []
    for relay in intent.transparent_relays:
        exit_ = exits[relay.exit_ref]
        path = next(path for path in exit_.paths if path.id == relay.protocol_path_ref)
        first_hop = servers[relay.hop_server_refs[0]]
        exit_server = servers[exit_.server_ref]
        workload = _active_workload(cluster, exit_.id)
        host_ref = f"host:{relay.id}:{path.id}"
        host_uuid = workload.host_uuids.get(host_ref, "") if workload else ""
        relays.append(
            RelayEntry(
                ip=first_hop.host,
                name=relay.id,
                port=relay.listen_port,
                exit_node_ip=exit_server.host,
                host_uuids={path.protocol: host_uuid} if host_uuid else {},
                sni=relay.reality_sni or path.reality_sni or path.tls_sni,
                ssh_user=first_hop.user,
                ssh_port=first_hop.port,
            )
        )
    # Only the advertised first hop is externally health-checkable. Internal
    # hops may be source-restricted, so exposing them as public relays lies.
    return relays


def _v4_members(
    cluster: ClusterConfig,
    servers: dict[str, ServerEntry],
) -> list[TopologyServerMember]:
    """Project every referenced V4 server, including private relay hops."""
    intent = cluster.topology_intent
    if intent is None:
        return []
    roles: dict[str, list[str]] = {}
    relay_hops: dict[str, list[RelayHopMembership]] = {}

    def add_role(server_ref: str, role: str) -> None:
        values = roles.setdefault(server_ref, [])
        if role not in values:
            values.append(role)

    add_role(intent.control.server_ref, "panel")
    for exit_ in intent.exits:
        add_role(exit_.server_ref, "exit")
    for gateway in intent.routing_gateways:
        add_role(gateway.server_ref, "routing_gateway")
    for relay in intent.transparent_relays:
        for position, server_ref in enumerate(relay.hop_server_refs, start=1):
            add_role(server_ref, "relay")
            relay_hops.setdefault(server_ref, []).append(
                RelayHopMembership(
                    chain=relay.id,
                    position=position,
                    advertised=position == 1,
                )
            )

    return [
        TopologyServerMember(
            id=server_ref,
            ip=servers[server_ref].host,
            name=servers[server_ref].name,
            roles=role_values,  # type: ignore[arg-type]
            ssh_user=servers[server_ref].user,
            ssh_port=servers[server_ref].port,
            relay_hops=relay_hops.get(server_ref, []),
        )
        for server_ref, role_values in roles.items()
    ]
