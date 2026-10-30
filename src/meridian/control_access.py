"""Meridian's control service behind the panel surface that client commands, pages, and tests use.

`client` commands, connection pages, and `meridian test` read and change users
through a small panel subset (`get_user`, `list_users`, `fetch_subscription`,
`enable_user`, `disable_user`, `update_user_limits`, `reset_user_traffic`,
`revoke_user_subscription`). This adapter answers
it from the control service with Remnawave's types and errors, so those paths
keep one implementation until the panel is removed (#95).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from functools import partial
from typing import Self, TypeVar

from meridian.cluster import ClusterConfig
from meridian.control.client import (
    ControlClient,
    ControlServiceAuthError,
    ControlServiceError,
    ControlServiceHistoryOff,
    ControlServiceScopeError,
)
from meridian.control.models import (
    LimitStrategy,
    PrivacySettings,
    PrivacyState,
    PrivacyUpdateResult,
    TrafficDailySummary,
    UserDevices,
    UserRecord,
    UserUpdate,
    UserUsage,
)
from meridian.core.attention import AttentionList
from meridian.core.client_devices import DevicesRemoved
from meridian.core.client_limits import ClientLimitChange, TrafficReset
from meridian.core.fleet import FleetTopology
from meridian.core.traffic import NodeTrafficEvidence
from meridian.remnawave import (
    XRAY_JSON_CLIENT_TYPE,
    Host,
    Node,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    SubscriptionDocument,
    User,
)

T = TypeVar("T")
_RESET_STRATEGIES: dict[TrafficReset, LimitStrategy] = {
    "never": "no_reset",
    "day": "day",
    "week": "week",
    "month": "month",
}


class ServiceAccessPanel:
    """Client reads and writes (users, limits, subscriptions) served by the control service."""

    def __init__(self, url: str, token: str, timeout: float = 20.0) -> None:
        self._client = ControlClient(url, token, timeout=max(1, math.ceil(timeout)))
        # Panel calls name users by ID; the service names them by username.
        self._usernames: dict[str, str] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    def get_user(self, username: str) -> User | None:
        record = _translated(lambda: self._client.get_user(username))
        return self._user(record) if record is not None else None

    def list_users(self) -> list[User]:
        return [self._user(record) for record in _translated(self._client.list_users)]

    def privacy(self) -> PrivacyState:
        return _translated(self._client.privacy)

    def set_privacy(self, settings: PrivacySettings) -> PrivacyUpdateResult:
        return _translated(lambda: self._client.set_privacy(settings))

    def user_usage(self, username: str, *, days: int = 30) -> UserUsage:
        return _translated(lambda: self._client.user_usage(username, days=days))

    def daily_traffic(self, *, days: int = 30) -> TrafficDailySummary:
        return _translated(lambda: self._client.daily_traffic(days=days))

    def recent_usage(self, username: str) -> UserUsage | None:
        """Seven days for `client show`, absent when the owner keeps history off (including a concurrent switch)."""

        def read() -> UserUsage | None:
            try:
                return self._client.user_usage(username, days=7)
            except ControlServiceHistoryOff:
                return None

        return _translated(read)

    def attention(self) -> AttentionList:
        return _translated(self._client.attention)

    def fetch_subscription(self, short_uuid: str, *, client_type: str = "") -> SubscriptionDocument:
        # The service names its documents by suffix (`/sub/TOKEN/mihomo`); anything else gets the Base64 list.
        document = client_type if client_type in (XRAY_JSON_CLIENT_TYPE, "mihomo", "singbox") else ""
        content = _translated(lambda: self._client.fetch_subscription(short_uuid, document=document))
        return SubscriptionDocument(url=f"sub/{short_uuid}", content=content, client_type=client_type)

    def enable_user(self, user_id: str) -> User:
        """Enable the user; the service at once re-applies expiry and quota, so the answer may be EXPIRED or LIMITED."""
        return self._update(user_id, UserUpdate(status="active"))

    def disable_user(self, user_id: str) -> User:
        return self._update(user_id, UserUpdate(status="disabled"))

    def update_user_limits(self, user_id: str, change: ClientLimitChange) -> User:
        """Set the limits the change names; the service lifts `limited`/`expired` once they allow the user."""
        fields: dict[str, object] = {}
        if change.traffic_limit_bytes is not None:
            fields["traffic_limit_bytes"] = change.traffic_limit_bytes
        if change.traffic_reset is not None:
            fields["traffic_limit_strategy"] = _RESET_STRATEGIES[change.traffic_reset]
        if change.sets_expiry:
            fields["expire_at"] = change.expires_at  # None clears the expiry
        if change.sets_devices:
            fields["device_limit"] = change.device_limit
        username = self._username(user_id)
        update = UserUpdate.model_validate(fields)
        return self._user(_translated(lambda: self._client.update_user(username, update)))

    def reset_user_traffic(self, user_id: str) -> User:
        username = self._username(user_id)
        return self._user(_translated(lambda: self._client.reset_user_traffic(username)))

    def extend_user(self, user_id: str, *, days: int) -> User:
        """Extend expiry and return the effective status after service enforcement."""
        username = self._username(user_id)
        return self._user(_translated(lambda: self._client.extend_user(username, days=days)))

    def revoke_user_subscription(self, user_id: str) -> User:
        username = self._username(user_id)
        return self._user(_translated(lambda: self._client.reissue_user(username)))

    def user_devices(self, user_id: str) -> UserDevices:
        username = self._username(user_id)
        return _translated(lambda: self._client.user_devices(username))

    def delete_user_devices(self, user_id: str, device_key: str | None = None) -> DevicesRemoved:
        username = self._username(user_id)
        return _translated(lambda: self._client.delete_user_devices(username, device_key))

    def _update(self, user_id: str, update: UserUpdate) -> User:
        username = self._username(user_id)
        return self._user(_translated(lambda: self._client.update_user(username, update)))

    def _username(self, user_id: str) -> str:
        username = self._usernames.get(user_id)
        if username is None:
            # Every caller reads the user first; an unknown ID is a programming error, not a missing user.
            raise RemnawaveError(f"User {user_id} was not read through this connection.", category="bug")
        return username

    def _user(self, record: UserRecord) -> User:
        self._usernames[record.user_id] = record.username
        return User(
            id=record.user_id,
            short_uuid=record.sub_token,
            username=record.username,
            vless_uuid=record.uuid,
            status=record.status.upper(),
            used_traffic_bytes=record.used_traffic_bytes,
            lifetime_used_traffic_bytes=record.lifetime_traffic_bytes,
            last_traffic_reset_at=record.traffic_reset_at or "",
            traffic_limit_bytes=record.traffic_limit_bytes,
            traffic_limit_strategy=record.traffic_limit_strategy.upper(),
            expire_at=record.expire_at or "",
            created_at=record.created_at,
            online_at=record.online_at or "",
            first_traffic_at=record.first_traffic_at,
            last_traffic_node=record.last_traffic_node,
            hwid_device_limit=record.device_limit,
        )


class ServiceFleetPanel(ServiceAccessPanel):
    """Fleet status from the control service: service health, node agents, and published relays.

    The service names nodes and endpoints by their topology IDs, which the
    fleet projection carries.
    """

    def __init__(self, url: str, token: str, *, node_ids: Sequence[str], endpoint_ids: Sequence[str]) -> None:
        super().__init__(url, token)
        self._node_ids = list(node_ids)
        self._endpoint_ids = list(endpoint_ids)

    def ping(self) -> bool:
        """False only when the service cannot be reached; a refused token raises, so status stops on it."""
        try:
            _translated(self._client.health)
        except RemnawaveNetworkError:
            return False
        return True

    def list_nodes(self) -> list[Node]:
        nodes: list[Node] = []
        for node_id in self._node_ids:
            status = _translated(partial(self._client.node, node_id))
            if status is not None:
                live = status.is_live()
                nodes.append(
                    Node(
                        uuid=node_id,
                        name=node_id,
                        is_connected=live,
                        apply_failed=live and bool(status.failed),
                        runtime=status.runtime,
                        traffic_used=status.traffic_bytes,
                        runtime_available=status.runtime_available(),
                        in_sync=status.in_sync,
                        failed=status.failed,
                        errors=status.errors,
                        xray_version=status.runtime.versions.xray or "" if status.runtime else "",
                    )
                )
        return nodes

    def list_hosts(self) -> list[Host]:
        hosts: list[Host] = []
        for endpoint_id in self._endpoint_ids:
            endpoint = _translated(partial(self._client.get_endpoint, endpoint_id))
            if endpoint is not None:
                hosts.append(
                    Host(uuid=endpoint_id, remark=endpoint.remark, address=endpoint.address, port=endpoint.port)
                )
        return hosts

    def node_evidence(self) -> dict[str, NodeTrafficEvidence]:
        """Aggregate activity for status readers, kept beside the existing fleet projection."""
        evidence: dict[str, NodeTrafficEvidence] = {}
        for node_id in self._node_ids:
            status = _translated(partial(self._client.node, node_id))
            if status is not None:
                evidence[node_id] = status.traffic_evidence()
        return evidence


AccessPanel = ServiceAccessPanel
FleetPanel = ServiceFleetPanel


def access_panel(cluster: ClusterConfig) -> AccessPanel:
    """The fleet's user directory: Meridian's control service."""
    return ServiceAccessPanel(cluster.panel.url, cluster.panel.api_token)


def fleet_panel(cluster: ClusterConfig, topology: FleetTopology) -> FleetPanel:
    """The fleet's live node and relay status, from the control service."""
    return ServiceFleetPanel(
        cluster.panel.url,
        cluster.panel.api_token,
        node_ids=[node.uuid for node in topology.nodes if node.uuid],
        endpoint_ids=[ref.uuid for relay in topology.relays for ref in relay.host_refs],
    )


def _translated(call: Callable[[], T]) -> T:
    try:
        return call()
    except (ControlServiceAuthError, ControlServiceScopeError) as exc:
        raise RemnawaveAuthError(str(exc), hint=exc.hint, category="user") from exc
    except ControlServiceError as exc:
        if exc.retryable:
            raise RemnawaveNetworkError(str(exc), hint=exc.hint, category="system") from exc
        raise RemnawaveError(str(exc), hint=exc.hint, category=exc.category) from exc
