"""Deploy target resolution: request plus saved servers, without opening SSH."""

from __future__ import annotations

import pytest

from meridian.core.deploy import DeployRequest
from meridian.core.errors import MeridianError
from meridian.resolve import resolve_deploy_target
from meridian.servers import ServerEntry

_IP_B = "198.51.100.20"


class FakeRegistry:
    def __init__(self, entries: list[ServerEntry] | None = None) -> None:
        self.entries = entries or []

    def find(self, query: str) -> ServerEntry | None:
        for entry in self.entries:
            if entry.host == query or entry.title == query or entry.id == query:
                return entry
        return None


def test_resolve_registered_server_uses_registry_user_when_request_user_is_root() -> None:
    registry = FakeRegistry([ServerEntry(host=_IP_B, user="admin", name="edge")])

    target = resolve_deploy_target(DeployRequest(requested_server="edge"), registry)

    assert target.server_ip == _IP_B
    assert target.ssh_user == "admin"
    assert target.ssh_port == 22


def test_resolve_saved_server_uses_stable_id_title_and_port() -> None:
    entry = ServerEntry(host=_IP_B, user="admin", port=2222, name="Family VPN", id="srv-0a1b2c3d4e5f")
    registry = FakeRegistry([entry])

    by_id = resolve_deploy_target(DeployRequest(requested_server=entry.id), registry)
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
    with pytest.raises(MeridianError) as exc_info:
        resolve_deploy_target(DeployRequest(requested_server="edge"), FakeRegistry())

    assert str(exc_info.value) == "Server 'edge' not found"
    assert exc_info.value.category == "user"
    assert exc_info.value.hint == "See registered servers: meridian server list"
