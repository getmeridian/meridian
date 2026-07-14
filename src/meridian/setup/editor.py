"""Pure compatibility edits for imperative commands on V4 topology."""

from __future__ import annotations

import re

from meridian.core.topology import (
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
    TransparentRelayIntent,
)


def add_exit_to_intent(
    intent: SetupIntent,
    *,
    server_ref: str,
    title: str,
    reality_sni: str,
    tls_hostname: str = "",
) -> SetupIntent:
    """Return validated intent with one independent exit workload."""
    exit_id = unique_resource_id(
        "exit",
        title,
        {
            *[exit_.id for exit_ in intent.exits],
            *[relay.id for relay in intent.transparent_relays],
            *[gateway.id for gateway in intent.routing_gateways],
            *[pool.id for pool in intent.egress_pools],
            *[route.id for route in intent.routes],
        },
    )
    paths = [
        ProtocolPathIntent(
            id=f"{exit_id}-reality",
            protocol="reality",
            reality_sni=reality_sni,
        )
    ]
    if tls_hostname:
        paths.extend(
            [
                ProtocolPathIntent(
                    id=f"{exit_id}-xhttp",
                    protocol="xhttp",
                    tls_sni=tls_hostname,
                    host=tls_hostname,
                    path=f"/{exit_id}-xhttp",
                ),
                ProtocolPathIntent(
                    id=f"{exit_id}-wss",
                    protocol="wss",
                    tls_sni=tls_hostname,
                    host=tls_hostname,
                    path=f"/{exit_id}-ws",
                ),
                ProtocolPathIntent(
                    id=f"{exit_id}-hysteria2",
                    protocol="hysteria2",
                    tls_sni=tls_hostname,
                ),
            ]
        )
    payload = intent.model_dump(mode="json")
    payload["exits"] = [
        *payload["exits"],
        ExitIntent(
            id=exit_id,
            server_ref=server_ref,
            paths=paths,
        ).model_dump(mode="json"),
    ]
    return SetupIntent.model_validate(payload)


def add_relay_to_intent(
    intent: SetupIntent,
    *,
    server_ref: str,
    title: str,
    exit_ref: str,
    listen_port: int,
    reality_sni: str = "",
) -> SetupIntent:
    """Return validated intent with one single-hop transparent relay."""
    exit_ = next(
        (candidate for candidate in intent.exits if candidate.id == exit_ref),
        None,
    )
    if exit_ is None:
        raise ValueError(f"Exit {exit_ref!r} does not exist in V4 topology.")
    reality_path = next(path for path in exit_.paths if path.protocol == "reality")
    relay_id = unique_resource_id(
        "relay",
        title,
        {
            *[exit_.id for exit_ in intent.exits],
            *[relay.id for relay in intent.transparent_relays],
            *[gateway.id for gateway in intent.routing_gateways],
            *[pool.id for pool in intent.egress_pools],
            *[route.id for route in intent.routes],
        },
    )
    payload = intent.model_dump(mode="json")
    payload["transparent_relays"] = [
        *payload["transparent_relays"],
        TransparentRelayIntent(
            id=relay_id,
            hop_server_refs=[server_ref],
            exit_ref=exit_ref,
            protocol_path_ref=reality_path.id,
            listen_port=listen_port,
            reality_sni=reality_sni,
        ).model_dump(mode="json"),
    ]
    return SetupIntent.model_validate(payload)


def unique_resource_id(
    prefix: str,
    title: str,
    existing: set[str],
) -> str:
    """Build a stable readable resource ID without colliding."""
    slug = re.sub(
        r"[^a-z0-9_-]+",
        "-",
        title.strip().lower(),
    ).strip("-_")
    base = slug or prefix
    if not base.startswith(f"{prefix}-") and base != prefix:
        base = f"{prefix}-{base}"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
