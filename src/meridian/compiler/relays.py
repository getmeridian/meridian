"""Transparent Realm relay chains: per-relay exit ingress, hops, and advertised hosts."""

from __future__ import annotations

from dataclasses import dataclass

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.builder import PlanBuilder
from meridian.compiler.client_delivery import fingerprint_copy_fields
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.firewalls import add_firewall, stable_token
from meridian.compiler.models import (
    HostPayload,
    InboundPayload,
    NginxRouteSpec,
    RealmHopPayload,
    ResourcePostcondition,
)
from meridian.compiler.names import exit_places, host_remark
from meridian.compiler.self_steal import SelfStealDecoy
from meridian.core.topology import SetupIntent, path_fingerprints


@dataclass(frozen=True)
class RelayCamouflage:
    """A loopback Reality inbound on the exit that imitates one relay's own SNI."""

    exit_ref: str
    inbound_id: str
    payload: InboundPayload


def plan_relay_camouflage(intent: SetupIntent, ports: PortAllocator) -> dict[str, RelayCamouflage]:
    """Map each relay whose SNI differs from its exit path to an inbound with that camouflage.

    One Reality inbound forwards probes to exactly one site, so a relay name added to the
    exit's own inbound answered with the exit's certificate. Relays sharing an exit path
    and SNI share one inbound.
    """
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    by_name: dict[tuple[str, str, str], RelayCamouflage] = {}
    result: dict[str, RelayCamouflage] = {}
    for relay in sorted(intent.transparent_relays, key=lambda item: item.id):
        exit_ = exits[relay.exit_ref]
        path = next(path for path in exit_.paths if path.id == relay.protocol_path_ref)
        if path.protocol != "reality" or not relay.reality_sni or relay.reality_sni == path.reality_sni:
            continue
        key = (exit_.id, path.id, relay.reality_sni)
        if key not in by_name:
            token = stable_token(relay.reality_sni)
            port = ports.auxiliary_tcp(
                exit_.server_ref, f"relay-camouflage:{exit_.id}:{path.id}:{relay.reality_sni}", base=60000, size=4999
            )
            by_name[key] = RelayCamouflage(
                exit_ref=exit_.id,
                inbound_id=f"inbound:{exit_.id}:{path.id}:camouflage:{token}",
                payload=InboundPayload(
                    workload_ref=exit_.id,
                    protocol="reality",
                    purpose="relay",
                    tag=f"meridian-{exit_.id}-reality-{token}",
                    listen_port=port,
                    public_port=port,
                    reality_sni=relay.reality_sni,
                    reality_server_names=[relay.reality_sni],
                ),
            )
        result[relay.id] = by_name[key]
    return result


def register_relay_ingress(
    *,
    builder: PlanBuilder,
    ports: PortAllocator,
    intent: SetupIntent,
    runtime_by_exit: dict[str, str],
    inbound_by_path: dict[tuple[str, str], str],
    allocated_path_ports: dict[tuple[str, str], int],
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]],
    stream_dependencies: dict[tuple[str, int], set[str]],
    stream_fallbacks: dict[tuple[str, int], str],
    local_fallbacks: dict[tuple[str, int], int],
    camouflage: dict[str, RelayCamouflage],
    self_steal: dict[str, SelfStealDecoy],
) -> dict[str, int]:
    """Give each relay its own SNI listener on the exit, reachable only from its last hop.

    A relay forwards raw TCP. Pointed at the exit's public 443, a probe sent
    through the relay without SNI would reach the control plane's TLS server
    and see the exit's own certificate. The dedicated listener routes only the
    relay's Reality name and sends everything else to the camouflage site.
    """
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    ingress: dict[str, int] = {}
    for relay in sorted(intent.transparent_relays, key=lambda item: item.id):
        exit_ = exits[relay.exit_ref]
        path = next(path for path in exit_.paths if path.id == relay.protocol_path_ref)
        if path.protocol != "reality":
            continue  # non-Reality relay paths are rejected in _compile_relays
        port = ports.auxiliary_tcp(exit_.server_ref, f"relay-ingress:{relay.id}", base=50000, size=9999)
        key = (exit_.server_ref, port)
        own = camouflage.get(relay.id)
        sni = own.payload.reality_sni if own else path.reality_sni
        stream_routes[key].append(
            NginxRouteSpec(
                match="sni",
                server_names=[sni],
                backend_server_ref=exit_.server_ref,
                backend_port=own.payload.listen_port if own else allocated_path_ports[(exit_.id, path.id)],
                protocol="reality",
            )
        )
        # Anything else through the relay reaches the site its name imitates.
        decoy = None if own else self_steal.get(exit_.id)
        if decoy is not None:
            local_fallbacks[key] = decoy.port
            stream_dependencies[key].add(decoy.http_id)
        else:
            stream_fallbacks[key] = sni
        stream_dependencies[key].update(
            {runtime_by_exit[exit_.id], own.inbound_id if own else inbound_by_path[(exit_.id, path.id)]}
        )
        stream_dependencies[key].add(
            add_firewall(builder, exit_.server_ref, "tcp", port, source_server_refs=[relay.hop_server_refs[-1]])
        )
        ingress[relay.id] = port
    return ingress


def compile_relays(
    *,
    builder: PlanBuilder,
    intent: SetupIntent,
    runtime_by_exit: dict[str, str],
    inbound_by_path: dict[tuple[str, str], str],
    stream_ids: dict[tuple[str, int], str],
    relay_ingress: dict[str, int],
    camouflage: dict[str, RelayCamouflage],
) -> list[str]:
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    places = exit_places([(exit_.id, exit_.region) for exit_ in intent.exits])
    host_ids: list[str] = []
    relay_listeners: set[tuple[str, int]] = set()
    for relay in sorted(intent.transparent_relays, key=lambda item: item.id):
        exit_ = exits[relay.exit_ref]
        path = next(path for path in exit_.paths if path.id == relay.protocol_path_ref)
        if path.protocol == "hysteria2":
            raise TopologyCompileError(f"Relay {relay.id} cannot forward UDP Hysteria2.")
        downstream_ref = exit_.server_ref
        downstream_port = relay_ingress[relay.id]
        downstream_dependency = stream_ids[(exit_.server_ref, downstream_port)]
        hop_ids: dict[int, str] = {}
        for hop_index in reversed(range(len(relay.hop_server_refs))):
            server_ref = relay.hop_server_refs[hop_index]
            listener = (server_ref, relay.listen_port)
            if listener in relay_listeners:
                raise TopologyCompileError(
                    f"Relay listener {server_ref}:{relay.listen_port} is assigned more than once."
                )
            if listener in stream_ids:
                raise TopologyCompileError(
                    f"Relay {relay.id} cannot listen on {server_ref}:{relay.listen_port}; "
                    "nginx already owns that port there. Choose another listen port or relay server."
                )
            relay_listeners.add(listener)
            firewall_id = add_firewall(
                builder,
                server_ref,
                "tcp",
                relay.listen_port,
                source_server_refs=([] if hop_index == 0 else [relay.hop_server_refs[hop_index - 1]]),
            )
            hop_id = f"realm:{relay.id}:{hop_index}"
            builder.add(
                hop_id,
                RealmHopPayload(
                    chain_ref=relay.id,
                    hop_index=hop_index,
                    server_ref=server_ref,
                    listen_port=relay.listen_port,
                    target_server_ref=downstream_ref,
                    target_port=downstream_port,
                    advertised=hop_index == 0,
                ),
                dependencies=[downstream_dependency, firewall_id],
                postconditions=[
                    _exists(hop_id),
                    ResourcePostcondition(kind="listening", target_ref=hop_id, detail=str(relay.listen_port)),
                ],
            )
            hop_ids[hop_index] = hop_id
            downstream_ref = server_ref
            downstream_port = relay.listen_port
            downstream_dependency = hop_id

        first_hop_id = hop_ids[0]
        own = camouflage.get(relay.id)
        inbound_id = own.inbound_id if own else inbound_by_path[(exit_.id, path.id)]
        for index, fingerprint in enumerate(path_fingerprints(path.protocol, path.fingerprint)):
            host_id = f"host:{relay.id}:{path.id}" + ("" if index == 0 else f":{fingerprint}")
            host_ids.append(
                builder.add(
                    host_id,
                    HostPayload(
                        owner_ref=relay.id,
                        remark=host_remark(
                            path.protocol, fingerprint=fingerprint, place=places[exit_.id], via=relay.id
                        ),
                        node_ref=runtime_by_exit[exit_.id],
                        inbound_ref=inbound_id,
                        address_server_ref=relay.hop_server_refs[0],
                        public_port=relay.listen_port,
                        protocol=path.protocol,
                        sni=relay.reality_sni or path.reality_sni or path.tls_sni,
                        host=path.host,
                        path=f"/{path.path}" if path.path else "",
                        fingerprint=fingerprint,
                        security_layer="TLS" if path.protocol in {"xhttp", "wss"} else "DEFAULT",
                        **fingerprint_copy_fields(index),
                    ),
                    dependencies=[first_hop_id, inbound_id],
                )
            )
    return host_ids


def _exists(target_ref: str) -> ResourcePostcondition:
    return ResourcePostcondition(kind="exists", target_ref=target_ref)
