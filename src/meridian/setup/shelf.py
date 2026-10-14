"""Saved-server shelf used by setup role selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.core.errors import LocalStateError
from meridian.core.setup import SetupRoleSelection
from meridian.core.topology import ServerCapability, TopologyServerShelfItem
from meridian.servers import ServerEntry, ServerRegistry


class ServerShelf:
    """Expose stable saved-server cards and canonical setup selections."""

    def __init__(self, registry: ServerRegistry) -> None:
        self._registry = registry

    def items(self, roles: SetupRoleSelection | None = None) -> list[TopologyServerShelfItem]:
        """Return server cards enriched with current role assignments."""
        capabilities, regions = _role_metadata(roles)
        return [
            TopologyServerShelfItem(
                server_ref=entry.id,
                title=entry.title or entry.host,
                host=entry.host,
                ssh_user=entry.ssh_user,
                ssh_port=entry.ssh_port,
                validation_state=entry.auth_state,
                capabilities=capabilities.get(entry.id, []),
                region=regions.get(entry.id, ""),
                last_checked_at=entry.last_validated_at,
            )
            for entry in self._registry.list()
        ]

    def canonical_refs(self, queries: Sequence[str], *, require_ready: bool = True) -> list[str]:
        """Resolve human selectors to immutable profile IDs in the requested order."""
        if not queries:
            raise LocalStateError(
                "Setup needs at least one saved server.",
                hint="Add a server first, then select it from the server shelf.",
            )
        selected: list[ServerEntry] = []
        for query in queries:
            entry = self._registry.find(query)
            if entry is None:
                raise LocalStateError(
                    f"Saved server {query!r} was not found.",
                    hint="Refresh the server shelf or add the server before continuing setup.",
                )
            if require_ready and entry.auth_state not in {"validated", "key_ready"}:
                detail = f" Last error: {entry.last_error}" if entry.last_error else ""
                raise LocalStateError(
                    f"Saved server {entry.title!r} has not passed SSH validation.{detail}",
                    hint="Validate the server connection and key access, then retry.",
                )
            selected.append(entry)

        refs = [entry.id for entry in selected]
        if len(refs) != len(set(refs)):
            raise LocalStateError(
                "The same saved server was selected more than once.",
                hint="Choose each server card once; one server may still hold multiple roles.",
            )
        return refs


def _role_metadata(
    roles: SetupRoleSelection | None,
) -> tuple[Mapping[str, list[ServerCapability]], Mapping[str, str]]:
    if roles is None:
        return {}, {}
    capabilities: dict[str, list[ServerCapability]] = {}
    regions: dict[str, str] = {}

    def add(server_ref: str, capability: ServerCapability) -> None:
        values = capabilities.setdefault(server_ref, [])
        if capability not in values:
            values.append(capability)

    add(roles.control.server_ref, "panel")
    for exit_ in roles.exits:
        add(exit_.server_ref, "exit")
        if exit_.region:
            regions[exit_.server_ref] = exit_.region
    for relay in roles.transparent_relays:
        for server_ref in relay.hop_server_refs:
            add(server_ref, "relay")
    for gateway in roles.routing_gateways:
        add(gateway.server_ref, "routing_gateway")
    return capabilities, regions
