"""Tests for meridian-core client result models and services."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from meridian.core.clients import (
    build_client_add_result,
    build_client_list_result,
    build_client_remove_result,
    build_client_show_result,
    build_client_status_result,
)
from meridian.core.services.clients import ClientNotFoundError, collect_client_list, collect_client_show


@dataclass
class PanelUser:
    uuid: str = "user-uuid"
    short_uuid: str = "short-uuid"
    username: str = "alice"
    vless_uuid: str = "vless-uuid"
    status: str = "ACTIVE"
    used_traffic_bytes: int = 1024
    traffic_limit_bytes: int = 2048
    created_at: str = "2026-04-01T12:00:00Z"
    online_at: str = "2026-04-02T12:00:00Z"


class PanelClient:
    def __init__(self, users: list[PanelUser]) -> None:
        self.users = users
        self.requested_usernames: list[str] = []

    def __enter__(self) -> PanelClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def get_user(self, username: str) -> PanelUser | None:
        self.requested_usernames.append(username)
        return next((user for user in self.users if user.username == username), None)

    def list_users(self) -> list[PanelUser]:
        return self.users

    def get_subscription_url(self, short_uuid: str) -> str:
        return f"https://198.51.100.1/api/sub/{short_uuid}"


def test_client_list_result_counts_statuses() -> None:
    result = build_client_list_result(
        [
            PanelUser(username="alice", status="ACTIVE"),
            PanelUser(username="bob", status="DISABLED"),
            PanelUser(username="carol", status="LIMITED"),
            PanelUser(username="dave", status="EXPIRED"),
            PanelUser(username="erin", status="UNKNOWN"),
        ]
    )
    data = result.to_data()

    assert data["summary"] == {
        "clients": 5,
        "active": 1,
        "disabled": 1,
        "limited": 1,
        "expired": 1,
        "other": 1,
    }
    assert data["clients"][0]["traffic_used_bytes"] == 1024
    assert data["clients"][0]["last_seen"] == "2026-04-02T12:00:00Z"


def test_client_show_result_reports_handoff_availability_without_links() -> None:
    result = build_client_show_result(
        PanelUser(),
        share_url="https://example.org/private/vless-uuid/",
        subscription_url="https://198.51.100.1/api/sub/short-uuid",
    )
    data = result.to_data()

    assert data["client"]["username"] == "alice"
    assert "share_url" not in data["client"]
    assert "subscription_url" not in data["client"]
    assert data["handoff"] == {
        "share_available": True,
        "subscription_available": True,
        "redacted": True,
    }


def test_client_mutation_results_preserve_public_wire_shapes() -> None:
    added = build_client_add_result([("alice", PanelUser(status="ACTIVE"))])
    removed = build_client_remove_result("alice")
    enabled = build_client_status_result("alice", "active")
    disabled = build_client_status_result("alice", "disabled")

    assert added.to_data() == {"clients": [{"username": "alice", "uuid": "user-uuid", "status": "active"}]}
    assert removed.to_data() == {"client": {"username": "alice"}}
    assert enabled.to_data() == {"client": {"username": "alice", "status": "active"}}
    assert disabled.to_data() == {"client": {"username": "alice", "status": "disabled"}}


def test_collect_client_list_uses_panel_adapter() -> None:
    result = collect_client_list(PanelClient([PanelUser(username="alice"), PanelUser(username="bob")]))

    assert result.clients.summary.clients == 2
    assert [client.username for client in result.clients.clients] == ["alice", "bob"]


def test_collect_client_list_filters_users_outside_ownership_allowlist() -> None:
    result = collect_client_list(
        PanelClient([PanelUser(username="alice"), PanelUser(username="meridian-route-123")]),
        allowed_usernames=["alice"],
    )

    assert result.clients.summary.clients == 1
    assert [client.username for client in result.clients.clients] == ["alice"]


def test_collect_client_show_builds_subscription_and_share_urls() -> None:
    result = collect_client_show(
        PanelClient([PanelUser(username="alice")]),
        "alice",
        build_share_url=lambda user: f"https://example.org/connect/{user.vless_uuid}/",
    )

    assert result.client.client.username == "alice"
    assert result.client.handoff.share_available is True
    assert result.client.handoff.subscription_available is True
    assert result.share_url == "https://example.org/connect/vless-uuid/"
    assert result.subscription_url == "https://198.51.100.1/api/sub/short-uuid"


def test_collect_client_show_raises_not_found() -> None:
    with pytest.raises(ClientNotFoundError):
        collect_client_show(PanelClient([]), "alice")


def test_collect_client_show_does_not_look_up_user_outside_ownership_allowlist() -> None:
    panel = PanelClient([PanelUser(username="meridian-route-123")])

    with pytest.raises(ClientNotFoundError):
        collect_client_show(
            panel,
            "meridian-route-123",
            allowed_usernames=["alice"],
        )

    assert panel.requested_usernames == []
