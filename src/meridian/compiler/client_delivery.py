"""Compile capability-specific Host projections for canonical subscriptions."""

from __future__ import annotations

import hashlib

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.models import CompiledResource, HostPayload, make_resource

_VISIBLE_TAG = "MERIDIAN_V4_VISIBLE"
_XRAY_EDGE_TAG = "MERIDIAN_V4_XRAY_EDGE"
_XRAY_VIRTUAL_TAG = "MERIDIAN_V4_XRAY_VIRTUAL"


def compile_xray_delivery_hosts(
    builder: PlanBuilder,
    *,
    template_ref: str,
) -> list[str]:
    """Keep normal Hosts visible and add hidden Xray injection projections."""
    source_resources: list[tuple[CompiledResource, HostPayload]] = []
    for resource in builder.resources.values():
        payload = resource.payload
        if isinstance(payload, HostPayload) and payload.advertised and not payload.is_hidden:
            source_resources.append((resource, payload))
    source_resources.sort(key=lambda item: item[0].logical_id)
    if not source_resources:
        return []

    hidden_ids: list[str] = []
    for resource, source in source_resources:
        visible = source.model_copy(
            update={
                "remark": compact_host_remark(source.remark),
                "tags": sorted({*source.tags, _VISIBLE_TAG}),
                "exclude_from_subscription_types": sorted(
                    {
                        *source.exclude_from_subscription_types,
                        "XRAY_JSON",
                    }
                ),
            }
        )
        builder.resources[resource.logical_id] = make_resource(
            resource.logical_id,
            visible,
            dependencies=resource.dependencies,
            postconditions=resource.postconditions,
        )

        hidden_id = f"{resource.logical_id}:xray-edge"
        hidden_ids.append(
            builder.add(
                hidden_id,
                source.model_copy(
                    update={
                        "remark": compact_host_remark(f"M4 X / {resource.logical_id}"),
                        "tags": [_XRAY_EDGE_TAG],
                        "is_hidden": True,
                        "xray_json_template_ref": "",
                        "exclude_from_subscription_types": [
                            "MIHOMO",
                            "XRAY_BASE64",
                        ],
                    }
                ),
                dependencies=resource.dependencies,
            )
        )

    primary, primary_payload = source_resources[0]
    virtual_id = "host:xray:auto"
    builder.add(
        virtual_id,
        primary_payload.model_copy(
            update={
                "remark": "M4 / automatic fallback",
                "tags": [_XRAY_VIRTUAL_TAG],
                "is_hidden": False,
                "xray_json_template_ref": template_ref,
                "exclude_from_subscription_types": [
                    "MIHOMO",
                    "XRAY_BASE64",
                ],
            }
        ),
        dependencies=sorted({*primary.dependencies, template_ref}),
    )
    return [*hidden_ids, virtual_id]


def compact_host_remark(value: str) -> str:
    """Fit owned Host identities inside Remnawave's 40-character limit."""
    if len(value) <= 40:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{value[:30].rstrip()}-{digest}"
