"""Pure compatibility edits for imperative commands on V4 topology."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import ValidationError

from meridian.core.inputs import TOPOLOGY_ID_MAX_LENGTH
from meridian.core.topology import (
    AccessIntent,
    EgressOverrideAction,
    EgressOverrideIntent,
    EgressOverrideMatch,
    ExitIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.setup.default_paths import default_exit_paths


def add_access_users_to_intent(intent: SetupIntent, usernames: list[str]) -> SetupIntent:
    """Return validated intent with new access users in stable request order."""
    existing = set(intent.access.users)
    duplicates = [username for username in usernames if username in existing]
    if duplicates:
        raise ValueError("Access users already exist in V4 topology: " + ", ".join(duplicates))
    payload = intent.model_dump(mode="json")
    payload["access"] = AccessIntent(
        squad_name=intent.access.squad_name,
        users=[*intent.access.users, *usernames],
    ).model_dump(mode="json")
    return SetupIntent.model_validate(payload)


def remove_access_user_from_intent(intent: SetupIntent, username: str) -> SetupIntent:
    """Return validated intent without one access user; at least one user must remain."""
    if username not in intent.access.users:
        raise ValueError(f"Client '{username}' is not in the topology")
    remaining = [user for user in intent.access.users if user != username]
    if not remaining:
        raise ValueError("A fleet needs at least one client; add another before removing the last one")
    payload = intent.model_dump(mode="json")
    payload["access"] = AccessIntent(squad_name=intent.access.squad_name, users=remaining).model_dump(mode="json")
    return SetupIntent.model_validate(payload)


def add_exit_to_intent(
    intent: SetupIntent,
    *,
    server_ref: str,
    title: str,
    reality_sni: str,
    tls_hostname: str = "",
    address: str = "",
    region: str = "",
) -> SetupIntent:
    """Return validated intent with one independent exit workload offering every default method."""
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
    paths = default_exit_paths(exit_id, address=address, reality_sni=reality_sni, domain=tls_hostname)
    payload = intent.model_dump(mode="json")
    payload["exits"] = [
        *payload["exits"],
        ExitIntent(
            id=exit_id,
            server_ref=server_ref,
            region=region,
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


def remove_exit_from_intent(intent: SetupIntent, exit_id: str) -> SetupIntent:
    """Return validated intent without one exit; egress pools drop it, but none may become empty."""
    if not any(exit_.id == exit_id for exit_ in intent.exits):
        raise ValueError(f"Exit {exit_id!r} is not in the topology.")
    payload = intent.model_dump(mode="json")
    payload["exits"] = [exit_ for exit_ in payload["exits"] if exit_["id"] != exit_id]
    for pool in payload["egress_pools"]:
        pool["exit_refs"] = [ref for ref in pool["exit_refs"] if ref != exit_id]
        if not pool["exit_refs"]:
            raise ValueError(f"Egress pool {pool['id']!r} would have no exits left.")
    return _validated(payload)


def move_exit_in_intent(intent: SetupIntent, exit_id: str, server_ref: str) -> SetupIntent:
    """Return validated intent with one exit on another server; its ID, paths, and settings stay."""
    if not any(exit_.id == exit_id for exit_ in intent.exits):
        raise ValueError(f"Exit {exit_id!r} is not in the topology.")
    payload = intent.model_dump(mode="json")
    for exit_ in payload["exits"]:
        if exit_["id"] == exit_id:
            exit_["server_ref"] = server_ref
    return _validated(payload)


def remove_routing_gateway_from_intent(intent: SetupIntent, gateway_id: str) -> SetupIntent:
    """Return validated intent without one routing gateway and the rules scoped to it."""
    if not any(gateway.id == gateway_id for gateway in intent.routing_gateways):
        raise ValueError(f"Routing gateway {gateway_id!r} is not in the topology.")
    payload = intent.model_dump(mode="json")
    payload["routing_gateways"] = [item for item in payload["routing_gateways"] if item["id"] != gateway_id]
    payload["routes"] = [route for route in payload["routes"] if route["source_gateway_ref"] != gateway_id]
    return _validated(payload)


def remove_relays_from_intent(intent: SetupIntent, relay_ids: set[str]) -> SetupIntent:
    """Return validated intent without the named transparent relay chains."""
    missing = sorted(relay_ids - {relay.id for relay in intent.transparent_relays})
    if missing:
        raise ValueError("Relay chains are not in the topology: " + ", ".join(missing))
    payload = intent.model_dump(mode="json")
    payload["transparent_relays"] = [relay for relay in payload["transparent_relays"] if relay["id"] not in relay_ids]
    return _validated(payload)


def add_egress_override_to_intent(
    intent: SetupIntent,
    exit_id: str,
    *,
    action: EgressOverrideAction,
    values: list[str],
    override_id: str = "",
) -> tuple[SetupIntent, EgressOverrideIntent]:
    """Return validated intent with one more egress override on an exit, and that override.

    Values are bare domains, ``geosite:NAME``, or ``geoip:NAME``; one override holds one kind.
    """
    exit_ = _exit(intent, exit_id)
    kinds = {_override_kind(value) for value in values}
    if len(kinds) != 1:
        raise ValueError("Mix of domains, geosite:, and geoip: values; add each kind with its own command.")
    existing = {override.id for override in exit_.egress_overrides}
    if override_id in existing:
        raise ValueError(f"Exit {exit_id} already has an override named {override_id!r}.")
    try:
        override = EgressOverrideIntent(
            id=override_id or unique_resource_id(action, "", existing),
            match=kinds.pop(),
            values=values,
            action=action,
        )
    except ValidationError as exc:
        raise ValueError(str(exc.errors()[0].get("msg", exc)).removeprefix("Value error, ")) from exc
    payload = intent.model_dump(mode="json")
    for item in payload["exits"]:
        if item["id"] == exit_id:
            item["egress_overrides"].append(override.model_dump(mode="json"))
    return _validated(payload), override


def remove_egress_override_from_intent(
    intent: SetupIntent,
    exit_id: str,
    override_id: str,
) -> tuple[SetupIntent, EgressOverrideIntent]:
    """Return validated intent without one of an exit's egress overrides, and the removed override."""
    exit_ = _exit(intent, exit_id)
    removed = next((override for override in exit_.egress_overrides if override.id == override_id), None)
    if removed is None:
        raise ValueError(f"Exit {exit_id} has no override named {override_id!r}.")
    payload = intent.model_dump(mode="json")
    for item in payload["exits"]:
        if item["id"] == exit_id:
            item["egress_overrides"] = [entry for entry in item["egress_overrides"] if entry["id"] != override_id]
    return _validated(payload), removed


def _exit(intent: SetupIntent, exit_id: str) -> ExitIntent:
    exit_ = next((item for item in intent.exits if item.id == exit_id), None)
    if exit_ is None:
        raise ValueError(f"Exit {exit_id!r} is not in the topology.")
    return exit_


def _override_kind(value: str) -> EgressOverrideMatch:
    lowered = value.strip().lower()
    if lowered.startswith("geosite:"):
        return "geosite"
    if lowered.startswith("geoip:"):
        return "geoip"
    return "domain"


def _validated(payload: dict[str, Any]) -> SetupIntent:
    """Validate an edited intent, surfacing the first rule it breaks as a plain message."""
    try:
        return SetupIntent.model_validate(payload)
    except ValidationError as exc:
        message = str(exc.errors()[0].get("msg", exc)).removeprefix("Value error, ")
        raise ValueError(message) from exc


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
    if len(base) > TOPOLOGY_ID_MAX_LENGTH:
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
        base = f"{base[: TOPOLOGY_ID_MAX_LENGTH - len(digest) - 1].rstrip('-_')}-{digest}"
    candidate = base
    suffix = 2
    while candidate in existing:
        ending = f"-{suffix}"
        candidate = f"{base[: TOPOLOGY_ID_MAX_LENGTH - len(ending)].rstrip('-_')}{ending}"
        suffix += 1
    return candidate
