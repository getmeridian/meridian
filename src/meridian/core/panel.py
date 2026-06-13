"""Panel client protocol — the contract layer for Remnawave panel access.

``PanelClient`` captures the full public interface of ``MeridianPanel``
(in ``meridian.remnawave``).  Library modules, services, and the reconciler
depend on this protocol rather than the concrete implementation, enabling
test doubles without monkeypatching.

Narrow service-specific protocols (``FleetPanelClient``, ``ClientPanelClient``)
remain in ``core/services/`` for interface segregation — they are strict subsets
of ``PanelClient``.

Return types use ``Any`` because the concrete dataclasses (``User``, ``Node``,
``Host``, etc.) live in ``meridian.remnawave``, which ``core/`` must not import.
Structural protocols for individual entity shapes (``PanelUserLike``,
``ApiNodeLike``) are defined closer to their consumers in ``core/clients.py``
and ``core/fleet.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Self


@runtime_checkable
class PanelClient(Protocol):
    """Full panel API contract satisfied by ``MeridianPanel``.

    Every public method mirrors ``MeridianPanel`` in ``meridian.remnawave``
    so that any conforming object (including test doubles) can be used as a
    drop-in replacement.  Class-level auth helpers (``login``, ``register_admin``)
    are excluded — they are factory concerns, not instance operations.

    Return types are ``Any`` because the concrete dataclass definitions live
    in ``meridian.remnawave``.  Callers that need typed field access should
    use the structural protocols in ``core/clients.py`` and ``core/fleet.py``
    (``PanelUserLike``, ``ApiNodeLike``, etc.).
    """

    # --- Context manager ---

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...

    def close(self) -> None:
        """Close underlying HTTP clients."""
        ...

    # --- Health ---

    def ping(self) -> bool:
        """Check if the panel is reachable and authenticated."""
        ...

    # --- Users (= Meridian clients) ---

    def create_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int = 0,
        expire_at: str = "",
        squad_uuids: list[str] | None = None,
    ) -> Any:
        """Create a new user. Returns a ``User`` dataclass."""
        ...

    def get_user(self, username: str) -> Any | None:
        """Find a user by username. Returns ``User`` or ``None``."""
        ...

    def get_user_by_uuid(self, uuid: str) -> Any | None:
        """Find a user by UUID. Returns ``User`` or ``None``."""
        ...

    def list_users(self) -> list[Any]:
        """List all users. Returns ``list[User]``."""
        ...

    def delete_user(self, uuid: str) -> bool:
        """Delete a user by UUID. Returns ``False`` if not found."""
        ...

    def enable_user(self, uuid: str) -> None:
        """Enable a disabled user."""
        ...

    def disable_user(self, uuid: str) -> None:
        """Disable a user."""
        ...

    # --- Nodes ---

    def create_node(
        self,
        name: str,
        address: str,
        port: int,
        *,
        config_profile_uuid: str,
        inbound_uuids: list[str] | None = None,
        country_code: str = "XX",
    ) -> Any:
        """Register a new node. Returns a ``NodeCredentials`` dataclass."""
        ...

    def list_nodes(self) -> list[Any]:
        """List all registered nodes. Returns ``list[Node]``."""
        ...

    def find_node_by_address(self, address: str) -> Any | None:
        """Find a node by address. Returns ``Node`` or ``None``."""
        ...

    def get_node(self, uuid: str) -> Any | None:
        """Get a node by UUID. Returns ``Node`` or ``None``."""
        ...

    def enable_node(self, uuid: str) -> None:
        """Enable a disabled node."""
        ...

    def disable_node(self, uuid: str) -> None:
        """Disable a node."""
        ...

    def restart_node(self, uuid: str) -> None:
        """Restart Xray on a node."""
        ...

    def delete_node(self, uuid: str) -> None:
        """Deregister a node."""
        ...

    def update_node_name(self, uuid: str, name: str) -> None:
        """Update a node's display name in the panel."""
        ...

    # --- Hosts (relays map here) ---

    def create_host(
        self,
        *,
        remark: str,
        address: str,
        port: int,
        config_profile_uuid: str,
        inbound_uuid: str,
        sni: str = "",
        host_header: str = "",
        path: str = "",
        alpn: str | None = None,
        fingerprint: str | None = None,
        security_layer: str = "DEFAULT",
        is_disabled: bool = False,
    ) -> Any:
        """Create a host entry (direct address or relay). Returns ``Host``."""
        ...

    def list_hosts(self) -> list[Any]:
        """List all host entries. Returns ``list[Host]``."""
        ...

    def find_host_by_remark(self, remark: str) -> Any | None:
        """Find a host by remark string. Returns ``Host`` or ``None``."""
        ...

    def enable_host(self, uuid: str) -> None:
        """Enable a disabled host."""
        ...

    def disable_host(self, uuid: str) -> None:
        """Disable a host (subscriptions auto-exclude)."""
        ...

    def delete_host(self, uuid: str) -> None:
        """Delete a host entry."""
        ...

    # --- Config Profiles ---

    def create_config_profile(self, name: str, config: dict[str, Any]) -> Any:
        """Create a config profile. Returns ``ConfigProfile``."""
        ...

    def get_config_profile(self, uuid: str) -> Any | None:
        """Get a config profile by UUID. Returns ``ConfigProfile`` or ``None``."""
        ...

    def list_config_profiles(self) -> list[Any]:
        """List all config profiles. Returns ``list[ConfigProfile]``."""
        ...

    def find_config_profile_by_name(self, name: str) -> Any | None:
        """Find a config profile by name. Returns ``ConfigProfile`` or ``None``."""
        ...

    # --- Inbounds ---

    def list_inbounds(self) -> list[Any]:
        """List all inbounds across config profiles. Returns ``list[Inbound]``."""
        ...

    # --- Internal Squads ---

    def list_internal_squads(self) -> list[dict[str, Any]]:
        """List internal squads. Returns raw dicts with uuid, name, info."""
        ...

    def assign_inbounds_to_squad(self, squad_uuid: str, inbound_uuids: list[str]) -> None:
        """Assign inbounds to an internal squad."""
        ...

    # --- Keygen ---

    def get_node_secret_key(self) -> str:
        """Fetch the node mTLS secret bundle."""
        ...

    # --- Subscriptions ---

    def get_subscription_url(self, short_uuid: str) -> str:
        """Build the subscription URL for a user."""
        ...

