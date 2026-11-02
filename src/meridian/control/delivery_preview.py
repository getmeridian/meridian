"""One account/endpoint view supplies live policy previews and explanations."""

from __future__ import annotations

from collections.abc import Callable

from meridian.control.access_memberships import eligible_endpoints
from meridian.control.delivery_policy_settings import require_delivery_policy
from meridian.control.formats.capabilities import requires_primary, supported_endpoints
from meridian.control.formats.entries import copy_groups, primaries
from meridian.control.models import ClientEndpoint, ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.core.delivery_capabilities import (
    GEODATA_SOURCE,
    DeliveryPolicyOmission,
    delivery_capabilities,
    represent_policy_dns,
    represent_policy_routes,
)
from meridian.core.delivery_explain import DeliveryExplainRequest, DeliveryExplanation, explain_delivery_policy
from meridian.core.delivery_geodata import GeoData
from meridian.core.delivery_matchers import DeliveryDns
from meridian.core.delivery_policies import DeliveryAction, ResolvedDeliveryPolicy
from meridian.core.delivery_preview import (
    DELIVERY_DOCUMENTS,
    DeliveryPolicyPreview,
    FormatDeliveryPolicy,
    route_preview,
)


def preview_delivery_policy(resolved: ResolvedDeliveryPolicy, endpoints: list[ClientEndpoint]) -> DeliveryPolicyPreview:
    formats = []
    for document in DELIVERY_DOCUMENTS:
        listed = supported_endpoints(endpoints, document)
        if requires_primary(document):
            listed = primaries(listed)
        exits = {endpoint.exit_ref for endpoint in listed if endpoint.exit_ref}
        routes, omitted = represent_policy_routes(resolved, document, available_exits=exits)
        resolvers, fake_ip, dns_omitted = represent_policy_dns(resolved, document)
        if document == "xray-json":
            for route in routes:
                if route.action != "exit":
                    continue
                count = sum(endpoint.exit_ref != route.exit_ref for endpoint in listed)
                if count:
                    omitted.append(
                        DeliveryPolicyOmission(
                            reason="exit_unavailable",
                            rule=route.number,
                            exit=route.exit_ref,
                            blocked=True,
                            context="xray_individual_profiles",
                            profile_count=count,
                        )
                    )
                count = sum(
                    not any(endpoint.exit_ref == route.exit_ref for endpoint in group)
                    for _, group in copy_groups(supported_endpoints(endpoints, document))
                )
                if count:
                    omitted.append(
                        DeliveryPolicyOmission(
                            reason="exit_unavailable",
                            rule=route.number,
                            exit=route.exit_ref,
                            blocked=True,
                            context="xray_fingerprint_profiles",
                            profile_count=count,
                        )
                    )
        formats.append(
            FormatDeliveryPolicy(
                format=document,
                capabilities=delivery_capabilities(document),
                rules=[route_preview(route) for route in routes],
                final_action=DeliveryAction(kind="proxy") if routes else None,
                dns=DeliveryDns(resolvers=resolvers, fake_ip=fake_ip) if resolvers else None,
                dns_source="custom" if resolvers else "omitted" if resolved.policy.dns is not None else "preset",
                omitted=[*omitted, *dns_omitted],
            )
        )
    return DeliveryPolicyPreview(effective=resolved, formats=formats, geodata_source=GEODATA_SOURCE)


def explain_person_delivery(
    store: Store,
    request: DeliveryExplainRequest,
    *,
    read_user: Callable[[str], UserRecord],
    read_settings: Callable[[], ServiceSettings],
    read_geodata: Callable[[], GeoData | None],
) -> DeliveryExplanation:
    with store.lock:
        record = read_user(request.person) if request.person is not None else None
        settings = read_settings()
        resolved = require_delivery_policy(
            settings,
            record.profiles if record is not None else [],
            record.delivery_policy if record is not None else None,
        )
        endpoints = [ClientEndpoint.model_validate_json(row[0]) for row in store.rows("SELECT body FROM endpoints")]
        if record is not None:
            endpoints = eligible_endpoints(record, endpoints, settings.access_profiles)
        listed = supported_endpoints(endpoints, request.format)
        if requires_primary(request.format):
            listed = primaries(listed)
        exits = {endpoint.exit_ref for endpoint in listed if endpoint.exit_ref}
    return explain_delivery_policy(
        resolved,
        request,
        available_exits=exits,
        geodata=read_geodata(),
        available=bool(listed),
        active=record is None or record.status == "active",
    )
