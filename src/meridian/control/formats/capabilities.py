"""The format omissions shared by live renderers and their operator preview."""

from __future__ import annotations

from meridian.control.models import ClientEndpoint
from meridian.core.subscription_formats import DocumentFormat
from meridian.core.subscription_preview import EndpointRestriction, OmissionReason


def endpoint_omission(
    endpoint: ClientEndpoint, document: DocumentFormat
) -> tuple[OmissionReason, EndpointRestriction | None] | None:
    if not endpoint.enabled:
        return "disabled_endpoint", None
    if endpoint.protocol == "xhttp" and document in ("stash", "singbox"):
        return "unsupported_format", "protocol"
    if endpoint.fingerprint_copy and document == "singbox":
        return "unsupported_format", "fingerprint_copy"
    return None


def supported_endpoints(endpoints: list[ClientEndpoint], document: DocumentFormat) -> list[ClientEndpoint]:
    return [endpoint for endpoint in endpoints if endpoint_omission(endpoint, document) is None]


def requires_primary(document: DocumentFormat) -> bool:
    return document in ("xray-json", "mihomo", "stash")
