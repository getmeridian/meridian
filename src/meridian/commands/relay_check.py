"""Per-hop health of relay chains, collected for `meridian check RELAY`."""

from __future__ import annotations

from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.commands._validation import validate_command_input
from meridian.console import fail
from meridian.core.command_inputs import RelayTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.relay_check import RelayCheckResult, RelayHopTarget
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.core.verification import VerificationCheck, aggregate_verification
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection


def collect_relay_check(relay_ip: str, user: str = "") -> RelayCheckResult:
    """Check every Realm hop a relay server runs: unit state, next-hop TCP, and the public entry."""
    from meridian.commands.relay import select_relay_chains
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.diagnostics.relay import check_realm_hop, check_relay_leg, check_relay_listener

    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay check request",
        relay_ip=relay_ip,
        user=user,
    )
    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    chains, server_ref, _label = select_relay_chains(intent, registry, request.relay_ip, "")
    try:
        hops = relay_hop_targets(intent, chains, registry, server_ref=server_ref)
    except LocalStateError as exc:
        fail(exc)

    connections: dict[str, ServerConnection] = {}
    checks: list[VerificationCheck] = []
    for hop in hops:
        if hop.public:
            checks.append(check_relay_listener(hop))
        if hop.server_ref not in connections:
            entry = registry.find(hop.server_ref)
            if entry is None:  # relay_hop_targets already resolved it
                raise RuntimeError(f"Saved server {hop.server_ref} disappeared during the check")
            connections[hop.server_ref] = connection_for_entry(entry, user=request.user)
        checks.append(check_realm_hop(connections[hop.server_ref], hop))
        checks.append(check_relay_leg(connections[hop.server_ref], hop))
    aggregate = aggregate_verification(checks)
    return RelayCheckResult(
        relay=request.relay_ip,
        chains=sorted(relay.id for relay in chains),
        hops=hops,
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
        checks=checks,
    )


def relay_hop_targets(
    intent: SetupIntent,
    chains: list[TransparentRelayIntent],
    registry: ServerRegistry,
    *,
    server_ref: str = "",
) -> list[RelayHopTarget]:
    """Resolve the compiled Realm hops of the chains, limited to one server when given."""
    from meridian.compiler import compile_topology
    from meridian.compiler.models import RealmHopPayload
    from meridian.reconciler.server_render import legacy_realm_service_name, realm_service_name

    chain_ids = {relay.id for relay in chains}
    camouflage = {relay.id: _camouflage_sni(intent, relay) for relay in chains}
    targets: list[RelayHopTarget] = []
    for resource in compile_topology(intent).resources:
        payload = resource.payload
        if not isinstance(payload, RealmHopPayload) or payload.chain_ref not in chain_ids:
            continue
        if server_ref and payload.server_ref != server_ref:
            continue
        hop_entry = _entry(registry, payload.server_ref)
        next_entry = _entry(registry, payload.target_server_ref)
        targets.append(
            RelayHopTarget(
                logical_id=resource.logical_id,
                chain_ref=payload.chain_ref,
                hop_index=payload.hop_index,
                server_ref=payload.server_ref,
                host=hop_entry.host,
                service=realm_service_name(resource.logical_id),
                legacy_service=legacy_realm_service_name(resource.logical_id),
                listen_port=payload.listen_port,
                public=payload.advertised,
                next_host=next_entry.host,
                next_port=payload.target_port,
                camouflage_sni=camouflage[payload.chain_ref],
            )
        )
    return sorted(targets, key=lambda hop: (hop.chain_ref, hop.hop_index))


def _camouflage_sni(intent: SetupIntent, relay: TransparentRelayIntent) -> str:
    """The Reality name clients send through this chain (the compiler's Host SNI)."""
    for exit_ in intent.exits:
        if exit_.id == relay.exit_ref:
            for path in exit_.paths:
                if path.id == relay.protocol_path_ref and path.protocol == "reality":
                    return relay.reality_sni or path.reality_sni
    return ""


def _entry(registry: ServerRegistry, server_ref: str) -> ServerEntry:
    entry = registry.find(server_ref)
    if entry is None:
        raise LocalStateError(
            f"Saved server {server_ref!r} is missing, so its relay hop cannot be checked.",
            hint="Add the server back with its SSH details, then rerun the check.",
        )
    return entry
