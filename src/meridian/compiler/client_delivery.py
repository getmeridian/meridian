"""Compile capability-specific Host projections for canonical subscriptions."""

from __future__ import annotations

import hashlib
from typing import Any

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.delivery import mihomo_template, singbox_template, xray_json_template
from meridian.compiler.models import (
    NON_XRAY_JSON_TYPES,
    CompiledResource,
    HostPayload,
    SubscriptionTemplatePayload,
    make_resource,
)
from meridian.compiler.names import REMNAWAVE_HOST_REMARK_MAX_LENGTH, fingerprint_label, subscription_template_name
from meridian.core.topology import PROTOCOL_PRIORITY, DeliveryIntent

_VISIBLE_TAG = "MERIDIAN_V4_VISIBLE"
XRAY_EDGE_TAG = "MERIDIAN_V4_XRAY_EDGE"
XRAY_ALT_EDGE_TAG = "MERIDIAN_V4_XRAY_ALT_EDGE"
ALT_FINGERPRINT_TAG = "MERIDIAN_V4_ALT_FINGERPRINT"
_XRAY_VIRTUAL_TAG = "MERIDIAN_V4_XRAY_VIRTUAL"
_EDGE_SUFFIX = ":xray-edge"


def fingerprint_copy_fields(index: int) -> dict[str, Any]:
    """Host fields for a path's fingerprint copies (every entry after its own fingerprint).

    Clients that health-check every entry must never switch fingerprints towards one server:
    TSPU stretches a 2-minute freeze to 10 minutes when another fingerprint arrives during it.
    Copies are tagged so failover groups leave them out, and skipped in sing-box, which has
    no way to keep them out of its automatic group.
    """
    if index == 0:
        return {}
    return {"tags": [ALT_FINGERPRINT_TAG], "exclude_from_subscription_types": ["SINGBOX"]}


def compile_delivery_templates(builder: PlanBuilder, delivery: DeliveryIntent, *, control_id: str) -> list[str]:
    """Add one managed template per requested format and the Xray JSON delivery Hosts.

    Returns the templates the External Squad assigns; the alternate-fingerprint Xray
    JSON template is reached only through its own virtual Host.
    """

    def add_template(suffix: str, name: str, template_type: Any, **content: Any) -> str:
        return builder.add(
            f"template:{delivery.template_name}:{suffix}",
            SubscriptionTemplatePayload(
                name=subscription_template_name(name, template_type),
                profile_title=delivery.profile_title,
                template_type=template_type,
                **content,
            ),
            dependencies=[control_id],
        )

    copies = _fingerprint_copies(builder)
    copy_remarks = sorted(compact_host_remark(payload.remark) for payload in copies)
    template_ids: list[str] = []
    xray_template_id = alt_template_id = ""
    if "xray_json" in delivery.formats:
        xray_template_id = add_template(
            "xray-json", delivery.template_name, "XRAY_JSON", template_json=xray_json_template()
        )
        template_ids.append(xray_template_id)
        if copies:
            alt_template_id = add_template(
                "xray-json-alt",
                f"{delivery.template_name}-alt",
                "XRAY_JSON",
                template_json=xray_json_template(edge_tag=XRAY_ALT_EDGE_TAG),
            )
    if "mihomo" in delivery.formats:
        template_ids.append(
            add_template(
                "mihomo",
                delivery.template_name,
                "MIHOMO",
                template_yaml=mihomo_template(exclude_from_failover=copy_remarks),
            )
        )
    if "singbox" in delivery.formats:
        template_ids.append(
            add_template("singbox", delivery.template_name, "SINGBOX", template_json=singbox_template())
        )
    if xray_template_id:
        labels = sorted({fingerprint_label(payload.fingerprint) for payload in copies})
        compile_xray_delivery_hosts(
            builder,
            template_ref=xray_template_id,
            alt_template_ref=alt_template_id,
            alt_remark=f"Automatic · {labels[0] if len(labels) == 1 else 'Backup'}",
        )
    return template_ids


def _fingerprint_copies(builder: PlanBuilder) -> list[HostPayload]:
    return [
        resource.payload
        for resource in builder.resources.values()
        if isinstance(resource.payload, HostPayload) and ALT_FINGERPRINT_TAG in resource.payload.tags
    ]


def compile_xray_delivery_hosts(
    builder: PlanBuilder,
    *,
    template_ref: str,
    alt_template_ref: str = "",
    alt_remark: str = "",
) -> list[str]:
    """Keep normal Hosts visible and add hidden Xray injection projections.

    Fingerprint copies feed a second virtual Host with its own balancer, so neither
    balancer ever mixes fingerprints.
    """
    source_resources: list[tuple[CompiledResource, HostPayload]] = []
    for resource in builder.resources.values():
        payload = resource.payload
        if isinstance(payload, HostPayload) and payload.advertised and not payload.is_hidden:
            source_resources.append((resource, payload))
    # The first source becomes the Xray JSON virtual Host, so it follows protocol priority too.
    source_resources.sort(key=lambda item: (PROTOCOL_PRIORITY.index(item[1].protocol), item[0].logical_id))
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

        hidden_id = f"{resource.logical_id}{_EDGE_SUFFIX}"
        hidden_ids.append(
            builder.add(
                hidden_id,
                source.model_copy(
                    update={
                        "remark": compact_host_remark(f"M4 X / {resource.logical_id}"),
                        "tags": [XRAY_ALT_EDGE_TAG if ALT_FINGERPRINT_TAG in source.tags else XRAY_EDGE_TAG],
                        "is_hidden": True,
                        "xray_json_template_ref": "",
                        "exclude_from_subscription_types": list(NON_XRAY_JSON_TYPES),
                    }
                ),
                dependencies=resource.dependencies,
            )
        )

    primaries = [item for item in source_resources if ALT_FINGERPRINT_TAG not in item[1].tags]
    copies = [item for item in source_resources if ALT_FINGERPRINT_TAG in item[1].tags]
    virtual_ids = [_virtual_host(builder, "host:xray:auto", "Automatic", primaries[0], template_ref)]
    if copies and alt_template_ref:
        virtual_ids.append(_virtual_host(builder, "host:xray:auto-alt", alt_remark, copies[0], alt_template_ref))
    return [*hidden_ids, *virtual_ids]


def _virtual_host(
    builder: PlanBuilder,
    logical_id: str,
    remark: str,
    source: tuple[CompiledResource, HostPayload],
    template_ref: str,
) -> str:
    resource, payload = source
    return builder.add(
        logical_id,
        payload.model_copy(
            update={
                "remark": remark,
                "tags": [_XRAY_VIRTUAL_TAG],
                "is_hidden": False,
                "xray_json_template_ref": template_ref,
                "exclude_from_subscription_types": list(NON_XRAY_JSON_TYPES),
            }
        ),
        dependencies=sorted({*resource.dependencies, template_ref}),
    )


def assign_host_positions(builder: PlanBuilder) -> None:
    """Order Hosts in every subscription by protocol priority, not by path ID.

    Clients default to the first entry and Mihomo falls back in order, so Reality leads and
    the UDP Hysteria2 path comes last.
    """
    hosts = [
        (resource, resource.payload)
        for resource in builder.resources.values()
        if isinstance(resource.payload, HostPayload)
    ]
    hosts.sort(
        key=lambda item: (
            PROTOCOL_PRIORITY.index(item[1].protocol),
            item[0].logical_id.removesuffix(_EDGE_SUFFIX),
            item[0].logical_id,
        )
    )
    for position, (resource, payload) in enumerate(hosts):
        builder.resources[resource.logical_id] = make_resource(
            resource.logical_id,
            payload.model_copy(update={"view_position": position}),
            dependencies=resource.dependencies,
            postconditions=resource.postconditions,
        )


def compact_host_remark(value: str) -> str:
    """Fit owned Host identities inside Remnawave's 100-character limit."""
    if len(value) <= REMNAWAVE_HOST_REMARK_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{value[: REMNAWAVE_HOST_REMARK_MAX_LENGTH - 10].rstrip()}-{digest}"
