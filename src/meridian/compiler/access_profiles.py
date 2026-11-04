"""Resolve reviewed person grants against the compiler's existing authentication identities."""

from __future__ import annotations

from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import CompiledResource, ConfigProfilePayload, HostPayload
from meridian.core.topology import SetupIntent


def validate_profile_backend(intent: SetupIntent, *, requested: bool = False) -> None:
    """Legacy compilation exists for migration; it cannot enforce profile membership or grants."""
    if intent.control.backend != "meridian" and (requested or intent.access_profiles):
        raise TopologyCompileError("Access profiles need Meridian's control service. Run meridian migrate first.")


def compile_access_profiles(intent: SetupIntent, resources: dict[str, CompiledResource]) -> dict[str, list[str]]:
    """An endpoint and its aliases grant the same inbound; gateway bridges never grant person access."""
    references: dict[str, list[str]] = {}
    for resource in resources.values():
        workload = resource.payload
        if isinstance(workload, ConfigProfilePayload):
            refs = [
                ref
                for ref, inbound in zip(workload.inbound_refs, workload.inbounds, strict=True)
                if inbound.purpose != "bridge"
            ]
            prefix = "gateway" if workload.workload_kind == "routing_gateway" else "exit"
            references[f"{prefix}:{workload.workload_id}"] = refs
            references.update({ref: [ref] for ref in refs})
    for logical_id, resource in resources.items():
        host = resource.payload
        if (
            isinstance(host, HostPayload)
            and host.inbound_ref in references
            and not host.is_hidden
            and "XRAY_BASE64" not in host.exclude_from_subscription_types
        ):
            references["endpoint:" + logical_id.removeprefix("host:")] = [host.inbound_ref]
    result = {}
    for profile in sorted(intent.access_profiles, key=lambda item: item.id):
        unknown = sorted(set(profile.grants) - references.keys())
        if unknown:
            # Exits and inbounds first: endpoint aliases are long and grant the same inbounds.
            offered = sorted(references, key=lambda ref: (ref.startswith("endpoint:"), ref))
            listed = ", ".join(offered[:24]) + (", …" if len(offered) > 24 else "")
            raise TopologyCompileError(
                f"Access profile {profile.id} references unknown or retired grants: {', '.join(unknown)}. "
                f"This fleet offers: {listed or 'nothing yet'}."
            )
        result[profile.id] = sorted({ref for grant in profile.grants for ref in references[grant]})
    return result
