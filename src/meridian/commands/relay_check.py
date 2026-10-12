"""`meridian relay check` -- per-hop health of relay chains."""

from __future__ import annotations

from collections.abc import Callable

import typer

from meridian.commands._helpers import load_topology_cluster
from meridian.commands._validation import validate_command_input
from meridian.commands.check_render import emit_verification_json, render_verification
from meridian.console import error_context, fail, is_json_mode
from meridian.core.command_inputs import RelayTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.relay_check import RelayCheckResult, RelayHopTarget
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.core.verification import VerificationCheck, aggregate_verification
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection

ConnectionFactory = Callable[[ServerEntry], ServerConnection]


def run_check(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
    *,
    connection_factory: ConnectionFactory | None = None,
) -> None:
    """Check every Realm hop a relay server runs (or every hop of one chain)."""
    operation = OperationContext()
    with error_context("relay.check", timer=operation.timer):
        result = collect_relay_check(relay_ip, exit_arg, user, connection_factory=connection_factory)
    if is_json_mode():
        _emit_json(result, operation)
    else:
        _render(result)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


def collect_relay_check(
    relay_ip: str,
    exit_arg: str,
    user: str,
    *,
    connection_factory: ConnectionFactory | None,
) -> RelayCheckResult:
    from meridian.commands.relay import select_relay_chains
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.diagnostics.relay import check_realm_hop, check_relay_listener

    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay check request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
    )
    cluster = load_topology_cluster()
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    chains, server_ref, _label = select_relay_chains(intent, registry, request.relay_ip, request.exit_arg)
    try:
        hops = relay_hop_targets(intent, chains, registry, server_ref=server_ref)
    except LocalStateError as exc:
        fail(exc)

    connect = connection_factory or _connection_for(request.user)
    connections: dict[str, ServerConnection] = {}
    checks: list[VerificationCheck] = []
    for hop in hops:
        if hop.public:
            checks.append(check_relay_listener(hop))
        if hop.server_ref not in connections:
            entry = registry.find(hop.server_ref)
            if entry is None:  # relay_hop_targets already resolved it
                raise RuntimeError(f"Saved server {hop.server_ref} disappeared during the check")
            connections[hop.server_ref] = connect(entry)
        checks.append(check_realm_hop(connections[hop.server_ref], hop))
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
    from meridian.reconciler.server_render import realm_service_name

    chain_ids = {relay.id for relay in chains}
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
                listen_port=payload.listen_port,
                public=payload.advertised,
                next_host=next_entry.host,
                next_port=payload.target_port,
            )
        )
    return sorted(targets, key=lambda hop: (hop.chain_ref, hop.hop_index))


def _entry(registry: ServerRegistry, server_ref: str) -> ServerEntry:
    entry = registry.find(server_ref)
    if entry is None:
        raise LocalStateError(
            f"Saved server {server_ref!r} is missing, so its relay hop cannot be checked.",
            hint="Add the server back with its SSH details, then rerun the check.",
        )
    return entry


def _connection_for(user_override: str) -> ConnectionFactory:
    def connect(entry: ServerEntry) -> ServerConnection:
        return connection_for_entry(entry, user=user_override)

    return connect


def _summary(result: RelayCheckResult) -> str:
    if result.verdict == "passed":
        return f"All {result.counts.checks} relay checks passed."
    if result.verdict == "findings":
        problems = result.counts.failed + result.counts.warnings
        return f"{problems} of {result.counts.checks} relay checks found problems."
    return f"{result.counts.skipped} of {result.counts.checks} relay checks could not collect evidence."


def _emit_json(result: RelayCheckResult, operation: OperationContext) -> None:
    emit_verification_json(
        command="relay.check",
        data=result.to_data(),
        verdict=result.verdict,
        counts=result.counts,
        exit_code=result.exit_code,
        summary=_summary(result),
        timer=operation.timer,
        inconclusive_code="MERIDIAN_RELAY_CHECK_INCONCLUSIVE",
        inconclusive_hint="Restore SSH access to every relay hop, then rerun the check.",
    )


def _render(result: RelayCheckResult) -> None:
    render_verification(
        "Relay check", f"{result.relay} - {', '.join(result.chains)}", result.checks, result.verdict, _summary(result)
    )
