"""Reviewed grants resolve to authentication inbounds, including public aliases."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.compiler import compile_topology
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import ClientEndpointPayload, NodeWorkloadPayload, ServiceSettingsPayload
from meridian.core.topology import SetupIntent
from tests.support.topology import gateway_fleet_intent, single_exit_intent


def intent_with_profiles(*grants: str) -> SetupIntent:
    return SetupIntent.model_validate(
        {**single_exit_intent().to_data(), "access_profiles": [{"id": "limited", "grants": list(grants)}]}
    )


def test_reviewed_grants_reach_workloads_and_delivery_with_one_inbound_identity():
    plan = compile_topology(intent_with_profiles("exit:exit-a"))
    workload = next(row.payload for row in plan.resources if isinstance(row.payload, NodeWorkloadPayload))
    settings = next(row.payload for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload))
    endpoints = [row.payload for row in plan.resources if isinstance(row.payload, ClientEndpointPayload)]
    assert workload.access_profiles == settings.access_profiles == {"limited": ["inbound:exit-a:reality-a"]}
    assert endpoints and {row.inbound_ref for row in endpoints} == {"inbound:exit-a:reality-a"}


@pytest.mark.parametrize("grant", ["exit:retired", "inbound:exit-a:retired", "endpoint:retired"])
def test_unknown_or_retired_grants_fail_compilation_with_the_profile_and_reference(grant: str):
    with pytest.raises(TopologyCompileError, match=f"limited.*{grant}"):
        compile_topology(intent_with_profiles(grant))


def test_an_unknown_grant_names_what_the_fleet_offers():
    with pytest.raises(TopologyCompileError, match=r"offers: exit:exit-a, inbound:exit-a:reality-a"):
        compile_topology(intent_with_profiles("inbound:nope"))


def test_a_public_endpoint_grant_covers_every_alias_of_its_authentication_inbound():
    base = compile_topology(single_exit_intent())
    endpoint = next(row for row in base.resources if isinstance(row.payload, ClientEndpointPayload))
    plan = compile_topology(intent_with_profiles(endpoint.logical_id))
    settings = next(row.payload for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload))
    assert settings.access_profiles == {"limited": ["inbound:exit-a:reality-a"]}


def test_gateway_bridges_cannot_be_granted_as_person_inbounds():
    intent = gateway_fleet_intent()
    bridge = next(
        ref
        for row in compile_topology(intent).resources
        if isinstance(row.payload, NodeWorkloadPayload)
        for ref, inbound in zip(row.payload.profile.inbound_refs, row.payload.profile.inbounds, strict=True)
        if inbound.purpose == "bridge"
    )
    updated = SetupIntent.model_validate(
        {**intent.to_data(), "access_profiles": [{"id": "limited", "grants": [bridge]}]}
    )
    with pytest.raises(TopologyCompileError, match="limited"):
        compile_topology(updated)


@pytest.mark.parametrize(
    "profiles",
    [
        [{"id": "default", "grants": ["exit:exit-a"]}],
        [{"id": "limited", "grants": []}, {"id": "limited", "grants": []}],
        [{"id": "limited", "grants": ["exit:exit-a", "exit:exit-a"]}],
    ],
)
def test_default_is_reserved_and_profile_names_and_grants_are_unique(profiles: list[dict]):
    with pytest.raises(ValidationError):
        SetupIntent.model_validate({**single_exit_intent().to_data(), "access_profiles": profiles})


def test_empty_profiles_leave_existing_compiled_payloads_identical():
    base = single_exit_intent()
    explicit = SetupIntent.model_validate({**base.to_data(), "access_profiles": []})
    assert explicit.to_data() == base.to_data()
    assert compile_topology(explicit).to_data() == compile_topology(base).to_data()


def test_initial_memberships_must_name_declared_profiles():
    data = single_exit_intent().to_data()
    from meridian.core.people_seeds import PeopleSeeds
    from tests.support.topology import people_draft

    seeds = PeopleSeeds.model_validate(
        {"users": ["default"], "creation_settings": {"default": {"profiles": ["retired"]}}}
    )
    with pytest.raises(ValueError, match="unknown"):
        people_draft(SetupIntent.model_validate(data), seeds).to_intent()
