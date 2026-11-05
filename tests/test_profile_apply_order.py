"""Policy activation follows stored references and precedes account creation in one apply."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import (
    ClientEndpointPayload,
    ControlUserPayload,
    NodeWorkloadPayload,
    ServiceSettingsPayload,
)
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.core.access_profile_views import AccessProfileReviewRequest
from meridian.core.access_profiles import AccessProfileIntent
from meridian.core.people_seeds import PeopleSeeds
from meridian.core.topology import SetupIntent
from meridian.people_adoption import ensure_people
from meridian.reconciler.agent_spec import build_node_spec
from meridian.reconciler.meridian_drivers import ClientEndpointDriver, ControlUserDriver, ServiceSettingsDriver
from meridian.reconciler.resources import build_resource_actions
from meridian.reconciler.workloads import LocalRealityKeyFactory
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import gateway_fleet_intent, single_exit_intent


@pytest.mark.parametrize("named_profiles", [False, True])
@pytest.mark.parametrize("builder", [single_exit_intent, gateway_fleet_intent])
def test_settings_follow_workloads_and_endpoints_and_accounts_follow_settings(builder, named_profiles):
    intent = builder()
    if named_profiles:
        intent = SetupIntent.model_validate(
            {**intent.to_data(), "access_profiles": [{"id": "limited", "grants": [f"exit:{intent.exits[0].id}"]}]}
        )
    plan = compile_topology(intent)
    order = {row.logical_id: index for index, row in enumerate(plan.resources)}
    settings_position = order["settings:service"]
    for row in plan.resources:
        if isinstance(row.payload, NodeWorkloadPayload | ClientEndpointPayload):
            assert order[row.logical_id] < settings_position, row.logical_id
        if isinstance(row.payload, ControlUserPayload):
            assert settings_position < order[row.logical_id], row.logical_id


@pytest.mark.parametrize(
    ("adding_profiles", "access_reviewed"),
    [(False, True), (True, False), (True, True)],
    ids=["reviewed-last-removal", "bootstrap-first-profile", "reviewed-first-profile"],
)
def test_one_apply_stages_references_and_creates_people_without_invalidating_its_settings_fence(
    tmp_path: Path, adding_profiles: bool, access_reviewed: bool
):
    profile = AccessProfileIntent(id="limited", grants=["exit:exit-a"])
    before = single_exit_intent(users=["existing"], access_profiles=[] if adding_profiles else [profile])
    after = single_exit_intent(users=["existing", "added"], access_profiles=[profile] if adding_profiles else [])
    previous, plan = compile_topology(before), compile_topology(after)
    keys = LocalRealityKeyFactory()("srv-exit")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=before)
        context = SimpleNamespace(
            cluster=cluster,
            client=lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False),
            reality_keys=lambda _workload, _generation: keys,
            address=lambda _server: "198.51.100.10",
            access_review_hash=None,
            persist=lambda _state: None,
        )
        endpoints = ClientEndpointDriver(context)
        for action in build_resource_actions(previous, 1):
            payload = action.resource.payload
            if isinstance(payload, NodeWorkloadPayload):
                admin.put_node(
                    payload.workload_ref,
                    build_node_spec(payload.profile, keys, arch="x86_64", access_profiles=payload.access_profiles),
                )
            if isinstance(payload, ClientEndpointPayload):
                endpoints.apply(action, None)
        old_settings = next(
            row.payload for row in previous.resources if isinstance(row.payload, ServiceSettingsPayload)
        )
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=old_settings.access_profiles))
        admin.put_user("existing")
        wanted_settings = next(row.payload for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload))
        wanted_endpoints = [row for row in plan.resources if isinstance(row.payload, ClientEndpointPayload)]
        if access_reviewed:
            review = admin.preview_access_profiles(
                AccessProfileReviewRequest(access_profiles=wanted_settings.access_profiles)
            )
            context.access_review_hash = review.access_hash
        for action in build_resource_actions(plan, 2):
            payload = action.resource.payload
            if isinstance(payload, NodeWorkloadPayload):
                admin.put_node(
                    payload.workload_ref,
                    build_node_spec(payload.profile, keys, arch="x86_64", access_profiles=payload.access_profiles),
                )
            elif isinstance(payload, ClientEndpointPayload):
                endpoints.apply(action, None)
            elif isinstance(payload, ServiceSettingsPayload):
                if adding_profiles:
                    assert {row.inbound_ref for row in admin.user_access("existing").inbounds} == {
                        "inbound:exit-a:reality-a"
                    }
                    for endpoint in wanted_endpoints:
                        assert admin.get_endpoint(endpoint.logical_id.removeprefix("endpoint:")).inbound_ref
                ServiceSettingsDriver(context).apply(action, None)
            elif isinstance(payload, ControlUserPayload):
                ControlUserDriver(context).apply(action, None)
        ensure_people(
            admin,
            PeopleSeeds.model_validate(
                {
                    "users": ["added"],
                    "creation_settings": {"added": {"profiles": ["limited"] if adding_profiles else ["default"]}},
                }
            ),
        )
        assert admin.settings().access_profiles == wanted_settings.access_profiles
        assert admin.get_user("added").profiles == (["limited"] if adding_profiles else ["default"])
