"""Per-hostname ACME certificate resources shared by topology compiler phases."""

from __future__ import annotations

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.firewalls import add_firewall, stable_token
from meridian.compiler.models import CertificatePayload


def ensure_certificate(
    builder: PlanBuilder,
    *,
    exit_id: str,
    server_ref: str,
    hostname: str,
    runtime_id: str,
) -> str:
    logical_id = f"certificate:{exit_id}:{stable_token(hostname)}"
    if logical_id in builder.resources:
        return logical_id
    challenge_firewall_id = add_firewall(builder, server_ref, "tcp", 80)
    # Issue after what the runtime waits for (its binding, hence the control plane), not after
    # the runtime itself: a Hysteria2 runtime needs the certificate before Xray can start.
    return builder.add(
        logical_id,
        CertificatePayload(server_ref=server_ref, hostname=hostname),
        dependencies=[*builder.resources[runtime_id].dependencies, challenge_firewall_id],
    )
