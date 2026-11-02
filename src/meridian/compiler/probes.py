"""Topology verification probes, compiled from the same reviewed resource graph."""

from __future__ import annotations

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.models import HostPayload, ProbePayload, ResourcePostcondition


def compile_probes(
    *,
    builder: PlanBuilder,
    runtime_by_exit: dict[str, str],
    host_ids: list[str],
    user_ids: list[str],
    settings_id: str,
    gateway_ids: list[str],
) -> None:
    for exit_ref, runtime_id in sorted(runtime_by_exit.items()):
        builder.add(
            f"probe:node:{exit_ref}",
            ProbePayload(probe="node", target_ref=runtime_id, expected="connected"),
            dependencies=[runtime_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=runtime_id)],
        )
    for host_id in sorted(host_ids):
        host = builder.resources[host_id].payload
        assert isinstance(host, HostPayload)
        builder.add(
            f"probe:{host_id}",
            ProbePayload(
                probe="listener",
                target_ref=host_id,
                protocol=host.protocol,
                expected="connect",
            ),
            dependencies=[host_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=host_id)],
        )
    for user_id in sorted(user_ids):
        builder.add(
            f"probe:subscription:{user_id.removeprefix('user:')}",
            ProbePayload(probe="subscription", target_ref=user_id, expected="canonical Remnawave subscription"),
            dependencies=[user_id, settings_id, *host_ids],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=user_id)],
        )
    for gateway_id in sorted(gateway_ids):
        builder.add(
            f"probe:route:{gateway_id.removeprefix('gateway:')}",
            ProbePayload(probe="route", target_ref=gateway_id, expected="ordered first-match routing"),
            dependencies=[gateway_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=gateway_id)],
        )
