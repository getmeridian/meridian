"""Tests for the command-free deploy Engine boundary."""

from __future__ import annotations

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.core.deploy import DeployRequest
from meridian.core.servers import ServerConnectionDraft, ServerProfile, profile_from_draft
from meridian.engine.deploy import (
    EngineError,
    dry_run_deploy_request,
    resolve_deploy_target,
)
from meridian.servers import ServerEntry
from meridian.setup.single_server import single_server_intent

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


def _configured_cluster(entry: ServerEntry) -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(
            url=f"https://{entry.host}/panel",
            api_token="test-token",
            server_ip=entry.host,
            secret_path="existing-secret-path",
            sub_path="existing-info-page",
        ),
        topology_intent=single_server_intent(None, server_ref=entry.id, domain="proxy.example.com"),
    )


def test_dry_run_explicit_ip_compiles_a_first_deploy_plan() -> None:
    plan = dry_run_deploy_request(DeployRequest(ip=_IP_A), cluster=ClusterConfig(), registry=FakeRegistry())

    assert plan.mode == "first_deploy"
    assert plan.server_ip == _IP_A
    assert plan.secret_path == ""
    assert plan.info_page_path == ""
    # XHTTP rides the IP certificate when there is no domain.
    assert len(plan.xhttp_path) == 16
    assert plan.ws_path == ""
    assert plan.ports.reality_port > 0
    assert plan.node_count == 1
    assert plan.relay_count == 0


def test_dry_run_with_domain_plans_tls_transport_paths() -> None:
    plan = dry_run_deploy_request(
        DeployRequest(ip=_IP_A, domain="proxy.example.com"),
        cluster=ClusterConfig(),
        registry=FakeRegistry(),
    )

    assert plan.xhttp_path
    assert plan.ws_path
    assert plan.ports.xhttp_port > 0
    assert plan.ports.wss_port > 0


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


def test_redeploy_reuses_saved_panel_paths_and_transport_paths() -> None:
    entry = ServerEntry(host=_IP_A, user="root", name="main", id="srv-0a1b2c3d4e5f")
    cluster = _configured_cluster(entry)
    assert cluster.topology_intent is not None
    saved_paths = {path.protocol: path.path for path in cluster.topology_intent.exits[0].paths}

    plan = dry_run_deploy_request(
        DeployRequest(ip=_IP_A, domain="proxy.example.com"),
        cluster=cluster,
        registry=FakeRegistry([entry]),
    )

    assert plan.mode == "redeploy"
    assert plan.secret_path == "existing-secret-path"
    assert plan.info_page_path == "existing-info-page"
    assert plan.xhttp_path == saved_paths["xhttp"]
    assert plan.ws_path == saved_paths["wss"]
    assert plan.node_count == 1


def test_existing_fleet_rejects_a_server_that_is_not_an_exit() -> None:
    entry = ServerEntry(host=_IP_A, user="root", name="main", id="srv-0a1b2c3d4e5f")

    with pytest.raises(EngineError) as exc_info:
        dry_run_deploy_request(
            DeployRequest(ip=_IP_B),
            cluster=_configured_cluster(entry),
            registry=FakeRegistry([entry]),
        )

    assert exc_info.value.category == "user"
    assert "meridian node add" in exc_info.value.hint


def test_cluster_without_topology_is_refused_with_rebuild_guidance() -> None:
    cluster = ClusterConfig(panel=PanelConfig(url=f"https://{_IP_A}/panel", api_token="test-token"))

    with pytest.raises(EngineError) as exc_info:
        dry_run_deploy_request(DeployRequest(ip=_IP_A), cluster=cluster, registry=FakeRegistry())

    assert "development build" in str(exc_info.value)
    assert "cluster.yml aside" in exc_info.value.hint
