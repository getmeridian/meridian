"""Profile use is durable before resource writes, after a fleet fence accepts the apply."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands.profile_review import ProfileReviewUnavailableError, profile_review_needed
from meridian.core.errors import LocalStateCorruptedError, LocalStateError, ProvisioningError
from meridian.core.setup import SetupDraft
from meridian.core.topology import AccessProfileIntent
from meridian.reconciler.fence import FleetFenceError
from meridian.reconciler.retirement import forget_server
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.runtime import SetupRuntime
from tests.support.mock_connection import MockConnection
from tests.support.topology import single_exit_intent


def profile_intent():
    return single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=["exit:exit-a"])])


def test_profile_use_survives_save_load_after_the_last_profile_is_removed(tmp_path: Path):
    path = tmp_path / "cluster.yml"
    cluster = ClusterConfig(topology_intent=profile_intent())
    cluster.access_profiles_used = True
    cluster.topology_intent = single_exit_intent()
    cluster.save(path)
    loaded = ClusterConfig.load(path)
    assert loaded.access_profiles_used is True
    assert loaded.topology_intent.access_profiles == []
    assert "access_profiles_used" not in loaded._extra
    loaded.save(path)
    assert ClusterConfig.load(path).access_profiles_used is True


@pytest.mark.parametrize("intent", [None, single_exit_intent()], ids=["empty", "without-profiles"])
def test_old_cluster_files_without_profiles_load_as_never_used(tmp_path: Path, intent):
    path = tmp_path / "cluster.yml"
    ClusterConfig(topology_intent=intent).save(path)
    data = yaml.safe_load(path.read_text())
    data.pop("access_profiles_used", None)
    path.write_text(yaml.safe_dump(data))
    assert ClusterConfig.load(path).access_profiles_used is False


@pytest.mark.parametrize("flag", [None, False], ids=["old-file", "false-flag"])
def test_a_saved_intent_with_profiles_counts_as_used_even_without_the_flag(tmp_path: Path, flag):
    path = tmp_path / "cluster.yml"
    ClusterConfig(topology_intent=profile_intent()).save(path)
    data = yaml.safe_load(path.read_text())
    if flag is None:
        data.pop("access_profiles_used", None)
    else:
        data["access_profiles_used"] = flag
    path.write_text(yaml.safe_dump(data))
    loaded = ClusterConfig.load(path)
    assert loaded.access_profiles_used is True
    loaded.topology_intent = single_exit_intent()
    loaded.save(path)
    assert ClusterConfig.load(path).access_profiles_used is True


def test_an_in_memory_profile_intent_refuses_an_unavailable_review():
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    cluster.topology_intent = profile_intent()
    with pytest.raises(ProfileReviewUnavailableError):
        profile_review_needed(cluster, single_exit_intent(), allow_bootstrap=True)


def test_saving_an_in_memory_profile_intent_serializes_effective_usage(tmp_path: Path):
    path = tmp_path / "cluster.yml"
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    cluster.topology_intent = profile_intent()
    cluster.save(path)
    assert yaml.safe_load(path.read_text())["access_profiles_used"] is True


@pytest.mark.parametrize("flag", [None, 0, "false", [], {}], ids=["null", "number", "string", "list", "mapping"])
def test_malformed_profile_usage_is_refused_instead_of_read_as_unused(tmp_path: Path, flag):
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump({"version": 4, "access_profiles_used": flag}))
    with pytest.raises(LocalStateCorruptedError, match="access_profiles_used must be a boolean"):
        ClusterConfig.load(path)


@pytest.mark.parametrize("entrypoint", ["draft", "intent"])
@pytest.mark.parametrize("interruption", ["fence", "resource"])
def test_profile_use_is_saved_with_accepted_intent_before_resources_even_when_apply_fails(
    tmp_path: Path, tmp_home: Path, entrypoint: str, interruption: str
):
    path = tmp_path / "cluster.yml"
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    cluster.save(path)
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    writes: list[str] = []

    def remote_write(stage: str):
        assert ClusterConfig.load(path).has_used_access_profiles is (stage == "resource")
        writes.append(stage)
        if stage == interruption:
            raise ProvisioningError("The apply was interrupted.")

    fence = MagicMock()
    fence.ensure.side_effect = lambda _local: remote_write("fence")
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=lambda state: state.save(path),
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )
    intent = profile_intent()
    draft = SetupDraft.from_intent(intent)
    draft = draft.model_copy(update={"review_hash": runtime.review(draft).review_hash})
    with (
        patch("meridian.setup.runtime.refuse_legacy_deployments"),
        patch.object(runtime, "_build_drivers", return_value={}),
        patch(
            "meridian.setup.runtime.execute_resource_plan", side_effect=lambda *_args, **_kw: remote_write("resource")
        ),
        pytest.raises(ProvisioningError, match="interrupted"),
    ):
        if entrypoint == "draft":
            runtime.apply(draft)
        else:
            runtime.apply_intent(intent, expected_plan_hash=draft.review_hash)
    assert writes == (["fence"] if interruption == "fence" else ["fence", "resource"])
    assert ClusterConfig.load(path).has_used_access_profiles is (interruption == "resource")


def test_a_profile_usage_save_failure_stops_before_resources(tmp_path: Path, tmp_home: Path):
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    fence = MagicMock()
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=MagicMock(side_effect=LocalStateError("Cannot save cluster.yml.")),
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )
    with (
        patch("meridian.setup.runtime.refuse_legacy_deployments"),
        patch("meridian.setup.runtime.execute_resource_plan") as execute,
        pytest.raises(LocalStateError, match="Cannot save"),
    ):
        runtime.apply_intent(profile_intent())
    fence.ensure.assert_called_once()
    execute.assert_not_called()


def test_a_refused_profile_apply_does_not_save_forgotten_bindings(tmp_path: Path, tmp_home: Path):
    path = tmp_path / "cluster.yml"
    binding = ManagedResourceBinding(
        logical_id="agent:old",
        resource_kind="agent_runtime",
        server_ref="srv-old",
        generation=1,
        active=True,
    )
    cluster = ClusterConfig(
        topology_intent=single_exit_intent(),
        managed_bindings={"agent:old@1": binding},
        active_generation=1,
    )
    cluster.save(path)
    saved = path.read_bytes()
    assert forget_server(cluster, [binding], "srv-old") == ["agent:old"]
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    fence = MagicMock()
    fence.ensure.side_effect = FleetFenceError("Another machine changed this fleet.")
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=lambda state: state.save(path),
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )
    with patch("meridian.setup.runtime.refuse_legacy_deployments"), pytest.raises(FleetFenceError):
        runtime.apply_intent(profile_intent())
    assert path.read_bytes() == saved
    assert ClusterConfig.load(path).managed_bindings["agent:old@1"].active is True
