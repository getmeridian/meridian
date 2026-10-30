"""Pure compatibility edits for imperative commands on V4 topology."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from meridian.core.client_limits import UserCreationSettings
from meridian.core.inputs import TOPOLOGY_ID_MAX_LENGTH, is_ip
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


def add_access_users_to_intent(
    intent: SetupIntent,
    usernames: list[str],
    display_names: Mapping[str, str] | None = None,
    *,
    creation_settings: Mapping[str, UserCreationSettings] | None = None,
) -> SetupIntent:
    """Return validated intent with new access users in stable request order."""
    existing = set(intent.access.users)
    duplicates = [username for username in usernames if username in existing]
    if duplicates:
        raise ValueError("Access users already exist in V4 topology: " + ", ".join(duplicates))
    payload = intent.model_dump(mode="json")
    payload["access"] = AccessIntent(
        squad_name=intent.access.squad_name,
        users=[*intent.access.users, *usernames],
        display_names={**intent.access.display_names, **(display_names or {})},
        creation_settings={**intent.access.creation_settings, **(creation_settings or {})},
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
    payload["access"] = AccessIntent(
        squad_name=intent.access.squad_name,
        users=remaining,
        display_names={user: name for user, name in intent.access.display_names.items() if user != username},
        creation_settings={
            user: settings for user, settings in intent.access.creation_settings.items() if user != username
        },
    ).model_dump(mode="json")
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
    self_steal: bool = False,
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
    paths = default_exit_paths(
        exit_id, address=address, reality_sni=reality_sni, domain=tls_hostname, self_steal=self_steal
    )
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


def move_exit_in_intent(
    intent: SetupIntent, exit_id: str, server_ref: str, *, old_address: str = "", new_address: str = ""
) -> SetupIntent:
    """Return validated intent with one exit on another server; its ID, paths, and settings stay.

    A bare-IP path names the old server's address as its certificate SNI and Host;
    those follow the exit to ``new_address`` (domains stay: their DNS is the operator's).
    """
    if not any(exit_.id == exit_id for exit_ in intent.exits):
        raise ValueError(f"Exit {exit_id!r} is not in the topology.")
    follows = is_ip(old_address) and is_ip(new_address)
    payload = intent.model_dump(mode="json")
    for exit_ in payload["exits"]:
        if exit_["id"] != exit_id:
            continue
        exit_["server_ref"] = server_ref
        for path in exit_["paths"] if follows else []:
            for field in ("tls_sni", "host"):
                if path.get(field) == old_address:
                    path[field] = new_address
    return _validated(payload)


def move_control_server_in_intent(
    intent: SetupIntent, server_ref: str, *, old_address: str = "", new_address: str = ""
) -> SetupIntent:
    """Return validated intent with the control plane, and every exit beside it, on another server.

    A relay hop or routing gateway on the old control server cannot move with it yet.
    """
    old = intent.control.server_ref
    if old == server_ref:
        return intent
    stuck = [gateway.id for gateway in intent.routing_gateways if gateway.server_ref == old]
    stuck += [relay.id for relay in intent.transparent_relays if old in relay.hop_server_refs]
    if stuck:
        raise ValueError(f"the control server also runs {', '.join(sorted(stuck))}, which cannot move with it")
    moved = intent
    for exit_ in intent.exits:
        if exit_.server_ref == old:
            moved = move_exit_in_intent(moved, exit_.id, server_ref, old_address=old_address, new_address=new_address)
    payload = moved.model_dump(mode="json")
    payload["control"]["server_ref"] = server_ref
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


def set_exit_reality_sni(intent: SetupIntent, exit_id: str, sni: str, *, self_steal: bool = False) -> SetupIntent:
    """Return validated intent with a new camouflage SNI on one exit's Reality path.

    ``self_steal`` marks the name as the operator's own, served by the exit itself.
    Relays without their own SNI and routing gateways built on the path follow it.
    """
    _exit(intent, exit_id)
    payload = intent.model_dump(mode="json")
    for exit_ in payload["exits"]:
        if exit_["id"] == exit_id:
            for path in exit_["paths"]:
                if path["protocol"] == "reality":
                    path["reality_sni"] = sni
                    path["self_steal"] = self_steal
    return _validated(payload)


def set_relay_reality_sni(intent: SetupIntent, relay_ids: set[str], sni: str) -> SetupIntent:
    """Return validated intent with a new camouflage SNI on the named relay chains.

    A chain whose new SNI equals its exit path's stores none, so it follows that path again.
    """
    missing = sorted(relay_ids - {relay.id for relay in intent.transparent_relays})
    if missing:
        raise ValueError("Relay chains are not in the topology: " + ", ".join(missing))
    path_snis = {(exit_.id, path.id): path.reality_sni for exit_ in intent.exits for path in exit_.paths}
    payload = intent.model_dump(mode="json")
    for relay in payload["transparent_relays"]:
        if relay["id"] in relay_ids:
            inherited = path_snis.get((relay["exit_ref"], relay["protocol_path_ref"]), "")
            relay["reality_sni"] = "" if sni == inherited else sni
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
        metadata = payload.pop("server_metadata", {})
        updated = SetupIntent.model_validate(payload)
        if not metadata:
            return updated
        refs = set(updated.declared_server_refs())
        payload["server_metadata"] = {ref: settings for ref, settings in metadata.items() if ref in refs}
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
