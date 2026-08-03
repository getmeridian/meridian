"""Tests for the command-free deploy Engine boundary."""

from __future__ import annotations

import pytest

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig, RelayEntry
from meridian.core.deploy import DeployRequest
from meridian.core.servers import ServerConnectionDraft, ServerProfile, profile_from_draft
from meridian.engine.deploy import (
    EngineError,
    dry_run_deploy_request,
    plan_deploy_request,
    resolve_deploy_target,
)
from meridian.servers import ServerEntry

_IP_A = "198.51.100.10"
_IP_B = "198.51.100.20"


class FakeRegistry:
    def __init__(self, entries: list[ServerEntry | ServerProfile] | None = None) -> None:
        self.entries = entries or []

    def find(self, query: str) -> ServerEntry | ServerProfile | None:
        for entry in self.entries:
            if entry.host == query or entry.title == query or getattr(entry, "id", "") == query:
                return entry
        return None


def _configured_cluster(ip: str = _IP_A) -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(
            url=f"https://{ip}/panel",
            api_token="test-token",
            server_ip=ip,
            secret_path="existing-secret-path",
            sub_path="existing-info-page",
        ),
        nodes=[
            NodeEntry(
                ip=ip,
                uuid="550e8400-e29b-41d4-a716-446655440000",
                xhttp_path="existing-xhttp-path",
                ws_path="existing-ws-path",
                is_panel_host=True,
            )
        ],
        relays=[RelayEntry(ip=_IP_B, exit_node_ip=ip)],
    )


def test_dry_run_explicit_ip_builds_deterministic_first_deploy_plan() -> None:
    plan = dry_run_deploy_request(DeployRequest(ip=_IP_A), cluster=ClusterConfig(), registry=FakeRegistry())

    assert plan.mode == "first_deploy"
    assert plan.server_ip == _IP_A
    assert plan.secret_path == "0" * 24
    assert plan.xhttp_path == "0" * 16
    assert plan.ws_path == "0" * 16
    assert plan.info_page_path == "0" * 16
    assert 30000 <= plan.ports.xhttp_port < 40000


def test_resolve_registered_server_uses_registry_user_when_request_user_is_root() -> None:
    registry = FakeRegistry([ServerEntry(host=_IP_B, user="admin", name="edge")])

    target = resolve_deploy_target(DeployRequest(requested_server="edge"), registry)

    assert target.server_ip == _IP_B
    assert target.ssh_user == "admin"
    assert target.ssh_port == 22


def test_resolve_server_profile_uses_stable_id_title_and_port() -> None:
    profile = profile_from_draft(ServerConnectionDraft(title="Family VPN", host=_IP_B, ssh_user="admin", ssh_port=2222))
    registry = FakeRegistry([profile])

    by_id = resolve_deploy_target(DeployRequest(requested_server=profile.id), registry)
    by_title = resolve_deploy_target(DeployRequest(requested_server="Family VPN"), registry)

    assert by_id.server_ip == _IP_B
    assert by_id.ssh_user == "admin"
    assert by_id.ssh_port == 2222
    assert by_title == by_id


def test_resolve_registered_server_preserves_explicit_user() -> None:
    registry = FakeRegistry([ServerEntry(host=_IP_B, user="admin", name="edge")])

    target = resolve_deploy_target(DeployRequest(requested_server="edge", user="deployer"), registry)

    assert target.server_ip == _IP_B
    assert target.ssh_user == "deployer"


def test_resolve_requested_server_ip_literal() -> None:
    target = resolve_deploy_target(DeployRequest(requested_server=_IP_B), FakeRegistry())

    assert target.server_ip == _IP_B
    assert target.ssh_user == "root"


@pytest.mark.parametrize("target", ["local", "locally"])
def test_resolve_local_targets_without_public_ip_detection(target: str) -> None:
    resolved = resolve_deploy_target(DeployRequest(ip=target), FakeRegistry())

    assert resolved.server_ip == target


def test_unknown_registered_server_raises_user_error_with_hint() -> None:
    with pytest.raises(EngineError) as exc_info:
        resolve_deploy_target(DeployRequest(requested_server="edge"), FakeRegistry())

    assert str(exc_info.value) == "Server 'edge' not found"
    assert exc_info.value.category == "user"
    assert exc_info.value.hint == "See registered servers: meridian server list"


def test_redeploy_reuses_existing_cluster_paths_and_counts() -> None:
    plan = dry_run_deploy_request(
        DeployRequest(ip=_IP_A),
        cluster=_configured_cluster(_IP_A),
        registry=FakeRegistry(),
    )

    assert plan.mode == "redeploy"
    assert plan.secret_path == "existing-secret-path"
    assert plan.xhttp_path == "existing-xhttp-path"
    assert plan.ws_path == "existing-ws-path"
    assert plan.info_page_path == "existing-info-page"
    assert plan.node_count == 1
    assert plan.relay_count == 1


def test_configured_cluster_rejects_new_ip_with_node_add_hint() -> None:
    with pytest.raises(EngineError) as exc_info:
        dry_run_deploy_request(DeployRequest(ip=_IP_B), cluster=_configured_cluster(_IP_A), registry=FakeRegistry())

    assert "Cluster already configured" in str(exc_info.value)
    assert "meridian node add" in exc_info.value.hint


def test_plan_deploy_request_accepts_injected_token_generator() -> None:
    tokens = iter(["panel-secret", "xhttp-path", "ws-path", "info-page"])

    plan = plan_deploy_request(
        DeployRequest(ip=_IP_A),
        cluster=ClusterConfig(),
        registry=FakeRegistry(),
        token_hex=lambda _size: next(tokens),
    )

    assert plan.secret_path == "panel-secret"
    assert plan.xhttp_path == "xhttp-path"
    assert plan.ws_path == "ws-path"
    assert plan.info_page_path == "info-page"
