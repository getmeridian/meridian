"""`--forget-unreachable`: forgetting a dead server's resources is saved only by the approved apply."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands._helpers import forget_unreachable_servers
from meridian.servers import ServerEntry, ServerRegistry
from tests.support.topology import single_exit_intent


def test_forgetting_stays_in_memory_until_the_apply_saves_it(tmp_home: Path) -> None:
    registry = ServerRegistry(tmp_home / "servers.json")
    registry.add(ServerEntry(host="198.51.100.7", name="gone", id="srv-gone"))
    binding = ManagedResourceBinding(
        logical_id="nginx:gone", resource_kind="nginx_artifact", generation=1, active=True, server_ref="srv-gone"
    )
    cluster = ClusterConfig(topology_intent=single_exit_intent(), managed_bindings={"nginx:gone@1": binding})
    cluster.save()
    runtime = SimpleNamespace(review=lambda *_args, **_kwargs: SimpleNamespace(retiring=[binding]))

    with (
        patch("meridian.commands._helpers.reviewed_runtime", return_value=runtime),
        patch("meridian.commands._helpers.ssh_port_answers", return_value=False),
    ):
        forget_unreachable_servers(cluster, registry, single_exit_intent(), forget=True, yes=True)

    assert not cluster.managed_bindings["nginx:gone@1"].active
    assert ClusterConfig.load().managed_bindings["nginx:gone@1"].active, "a declined review must leave it saved"
