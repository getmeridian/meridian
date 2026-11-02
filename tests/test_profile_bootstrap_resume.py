"""Fresh profile setup can resume until declarations may have reached the service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlServiceError
from meridian.core.cancellation import OperationCancellation, OperationCancelledError
from meridian.core.errors import ProvisioningError
from meridian.core.setup import SetupDraft
from meridian.core.topology import AccessProfileIntent
from meridian.reconciler.resources import ResourceExecutionResult, build_resource_actions
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.control_service import meridian_drivers
from meridian.setup.profile_review import ProfileReviewUnavailableError
from meridian.setup.runtime import SetupRuntime
from tests.support.mock_connection import MockConnection
from tests.support.topology import single_exit_intent


@pytest.mark.parametrize("entrypoint", ["draft", "intent"])
@pytest.mark.parametrize("interruption", ["apt-lock", "firewall", "cancelled", "acme"])
def test_fresh_profile_setup_resumes_after_an_interruption_before_settings(
    tmp_path: Path, tmp_home: Path, entrypoint: str, interruption: str
):
    path = tmp_path / "cluster.yml"
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    intent = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=["exit:exit-a"])])
    fence = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.access_profiles.side_effect = ControlServiceError("The service is not installed.", retryable=True)
    attempts = 0

    def execute(plan, state, drivers, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if interruption == "acme":
                state.panel = PanelConfig(url="https://198.51.100.10/control", api_token="test-admin-token")
                state.save(path)
            if interruption == "cancelled":
                raise OperationCancelledError(OperationCancellation(phase="apply", message="Setup was cancelled."))
            raise ProvisioningError("Setup was interrupted before service settings.")
        client.put_settings.side_effect = lambda *_args, **_kw: assert_usage_saved()
        action = next(row for row in build_resource_actions(plan, 1) if row.resource.logical_id == "settings:service")
        drivers["service_settings"].apply(action, None)
        return ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1)

    def assert_usage_saved():
        assert ClusterConfig.load(path).access_profiles_used is True

    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: ClusterConfig.load(path),
        persist=lambda state: state.save(path),
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )

    def apply():
        if entrypoint == "intent":
            return runtime.apply_intent(intent)
        draft = SetupDraft.from_intent(intent)
        return runtime.apply(draft.model_copy(update={"review_hash": runtime.review(draft).review_hash}))

    with (
        patch("meridian.setup.runtime.refuse_legacy_deployments"),
        patch("meridian.setup.profile_review.ControlClient", return_value=client),
        patch("meridian.setup.control_service.control_client", return_value=client),
        patch("meridian.setup.runtime.execute_resource_plan", side_effect=execute),
    ):
        with pytest.raises(OperationCancelledError if interruption == "cancelled" else ProvisioningError):
            apply()
        assert ClusterConfig.load(path).access_profiles_used is False
        assert apply().all_succeeded
        assert ClusterConfig.load(path).access_profiles_used is True
        with pytest.raises(ProfileReviewUnavailableError):
            apply()
    assert attempts == 2
    assert fence.ensure.call_count == 2
    client.put_settings.assert_called_once()


@pytest.mark.parametrize("unknown", [False, True], ids=["received", "reply-lost"])
def test_profile_use_is_saved_once_immediately_before_the_settings_write(tmp_path: Path, unknown: bool):
    from meridian.compiler import compile_topology
    from meridian.control.client import ControlServiceUncertain
    from meridian.core.errors import LocalStateError
    from meridian.reconciler.resources import UnknownResourceOutcome

    path = tmp_path / "cluster.yml"
    intent = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=[])])
    cluster = ClusterConfig(topology_intent=intent)
    cluster.save(path)
    plan = compile_topology(intent)
    client = MagicMock()
    client.__enter__.return_value = client
    events = []

    def persist(state):
        events.append("save")
        state.save(path)

    def put_settings(*_args, **_kwargs):
        events.append("write")
        assert ClusterConfig.load(path).access_profiles_used is True
        if unknown:
            raise ControlServiceUncertain("The settings reply was lost.")

    client.put_settings.side_effect = put_settings
    with patch("meridian.setup.control_service.control_client", return_value=client):
        driver = meridian_drivers(plan, cluster, persist=persist, connections={}, addresses={})["service_settings"]
        action = next(row for row in build_resource_actions(plan, 1) if row.resource.logical_id == "settings:service")
        if unknown:
            with pytest.raises(UnknownResourceOutcome):
                driver.apply(action, None)
        else:
            driver.apply(action, None)
        client.put_settings.side_effect = None
        driver.apply(action, None)
    assert events == ["save", "write"]
    assert ClusterConfig.load(path).access_profiles_used is True

    cluster.access_profiles_used = False
    with patch("meridian.setup.control_service.control_client", return_value=client):
        driver = meridian_drivers(
            plan,
            cluster,
            persist=MagicMock(side_effect=LocalStateError("Cannot save profile history.")),
            connections={},
            addresses={},
        )["service_settings"]
        with pytest.raises(LocalStateError, match="Cannot save"):
            driver.apply(action, None)
    assert client.put_settings.call_count == 2


def test_reviewed_apply_keeps_observed_profile_history_when_no_settings_write_is_reached(
    tmp_path: Path, tmp_home: Path
):
    from meridian.control.client import ControlClient
    from meridian.control.models import ServiceSettings
    from meridian.core.access_profile_views import AccessProfileReviewRequest
    from tests.support.control_service import ADMIN_TOKEN, serve_control

    path = tmp_path / "cluster.yml"
    intent = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=[])])
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20"))
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        review = admin.preview_access_profiles(AccessProfileReviewRequest(access_profiles={"limited": []}))
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        cluster.save(path)
        fence = MagicMock()
        fence.ensure.side_effect = lambda _view: assert_unused()

        def assert_unused():
            assert ClusterConfig.load(path).access_profiles_used is False

        def execute(*_args, **_kwargs):
            assert ClusterConfig.load(path).access_profiles_used is True
            raise ProvisioningError("The apply stopped before settings.")

        runtime = SetupRuntime(
            registry,
            cluster_loader=lambda: cluster,
            persist=lambda state: state.save(path),
            connection_builder=lambda _entry: MockConnection(),
            fence_factory=lambda *_args: fence,
            access_review_hash=review.access_hash,
        )
        with (
            patch("meridian.setup.runtime.refuse_legacy_deployments"),
            patch("meridian.setup.runtime.execute_resource_plan", side_effect=execute),
            pytest.raises(ProvisioningError),
        ):
            runtime.apply_intent(intent)
        assert ClusterConfig.load(path).access_profiles_used is True


def test_a_reviewed_removal_of_unsent_profiles_records_use_and_plans_converged(tmp_path: Path):
    """A reviewed write stamps the empty declarations fingerprint, so later unreviewed reads must expect it."""
    from meridian.compiler import compile_topology
    from meridian.control.client import ControlClient
    from meridian.core.access_profile_views import AccessProfileReviewRequest
    from tests.support.control_service import ADMIN_TOKEN, serve_control

    path = tmp_path / "cluster.yml"
    intent = single_exit_intent()
    plan = compile_topology(intent)
    action = next(row for row in build_resource_actions(plan, 1) if row.resource.logical_id == "settings:service")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        review = admin.preview_access_profiles(AccessProfileReviewRequest(access_profiles={}))
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=intent)
        cluster.save(path)

        def drivers(access_review_hash: str | None):
            with patch(
                "meridian.setup.control_service.control_client",
                side_effect=lambda state: ControlClient(url, ADMIN_TOKEN, report_mutations=False),
            ):
                return meridian_drivers(
                    plan,
                    cluster,
                    persist=lambda state: state.save(path),
                    connections={},
                    addresses={},
                    access_review_hash=access_review_hash,
                )["service_settings"]

        reviewed = drivers(review.access_hash)
        with patch(
            "meridian.setup.control_service.control_client",
            side_effect=lambda state: ControlClient(url, ADMIN_TOKEN, report_mutations=False),
        ):
            reviewed.apply(action, None)
            assert admin.settings().access_profile_declarations_hash is not None
            assert ClusterConfig.load(path).access_profiles_used is True
            assert drivers(None).observe(action, None).exists
