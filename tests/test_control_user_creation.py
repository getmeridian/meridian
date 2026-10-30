"""Reviewed user creation keeps first limits through lost replies and resumed applies."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ControlUserPayload, ResourcePlan, make_resource
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceUncertain
from meridian.control.models import UserCreate, UserRecord, UserUpdate
from meridian.core.topology import SetupIntent
from meridian.reconciler.meridian_drivers import ControlUserDriver
from meridian.reconciler.resource_executor import execute_resource_plan
from meridian.reconciler.resources import build_resource_actions, observation_converges
from meridian.setup.editor import add_access_users_to_intent, remove_access_user_from_intent
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import resource_plan

SETTINGS = {
    "expire_at": "2031-12-31T23:59:59Z",
    "traffic_limit_bytes": 50 * 1024**3,
    "traffic_limit_strategy": "month",
    "device_limit": 2,
}


def _intent() -> SetupIntent:
    payload = single_server_intent(None, server_ref="srv-a", client_name="carol").model_dump(mode="json")
    payload["access"]["creation_settings"] = {"carol": SETTINGS}
    return SetupIntent.model_validate(payload)


def _plan() -> ResourcePlan:
    user = next(
        resource for resource in compile_topology(_intent()).resources if resource.logical_id == "account:carol"
    )
    return resource_plan(make_resource(user.logical_id, user.payload, postconditions=user.postconditions))


def _settings(user: UserRecord) -> dict[str, object]:
    return {key: getattr(user, key) for key in SETTINGS}


def test_lost_creation_reply_resumes_with_the_limits_and_original_credentials(tmp_path: Path) -> None:
    plan = _plan()
    state = tmp_path / "cluster.yml"
    connection = SimpleNamespace(online=True, writes=0)

    class LostReplyClient(ControlClient):
        def get_user(self, username: str) -> UserRecord | None:
            if not connection.online:
                raise ControlServiceError("Control service is unreachable.", retryable=True)
            return super().get_user(username)

        def put_user(self, username: str) -> UserRecord:
            user = super().put_user(username)
            self.lose_reply()
            return user

        def create_user(self, create: UserCreate) -> UserRecord:
            user = super().create_user(create)
            self.lose_reply()
            return user

        def lose_reply(self) -> None:
            connection.writes += 1
            connection.online = False
            raise ControlServiceUncertain("The creation reply was lost.", retryable=True)

    with serve_control(tmp_path) as url:
        driver = ControlUserDriver(SimpleNamespace(client=lambda: LostReplyClient(url, ADMIN_TOKEN)))
        first = execute_resource_plan(
            plan,
            ClusterConfig(topology_intent=_intent()),
            {"control_user": driver},
            persist=lambda cluster: cluster.save(state),
        )
        assert not first.all_succeeded
        assert [checkpoint.status for checkpoint in ClusterConfig.load(state).action_checkpoints.values()] == [
            "unknown"
        ]
        with ControlClient(url, ADMIN_TOKEN) as client:
            created = client.get_user("carol")
        assert created is not None and _settings(created) == SETTINGS

        connection.online = True
        resumed = execute_resource_plan(
            ResourcePlan.model_validate_json(plan.model_dump_json()),
            ClusterConfig.load(state),
            {"control_user": driver},
            persist=lambda cluster: cluster.save(state),
        )
        with ControlClient(url, ADMIN_TOKEN) as client:
            after = client.get_user("carol")

    assert resumed.all_succeeded and connection.writes == 1
    assert after == created


def test_existing_user_keeps_operator_owned_settings_on_every_apply(tmp_path: Path) -> None:
    action = build_resource_actions(_plan(), 1)[0]
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.put_user("carol")
        before = client.update_user(
            "carol", UserUpdate(status="disabled", traffic_limit_bytes=17, expire_at=None, device_limit=5)
        )
        driver = ControlUserDriver(SimpleNamespace(client=lambda: ControlClient(url, ADMIN_TOKEN)))
        driver.apply(action, None)
        driver.apply(action, None)
        assert observation_converges(action, driver.observe(action, None))
        assert client.get_user("carol") == before


def test_default_user_keeps_its_original_reviewed_payload() -> None:
    intent = single_server_intent(None, server_ref="srv-a", client_name="carol")
    before = compile_topology(intent)
    payload = intent.model_dump(mode="json")
    payload["access"]["creation_settings"] = {}
    after = compile_topology(SetupIntent.model_validate(payload))
    user = next(resource for resource in after.resources if isinstance(resource.payload, ControlUserPayload))

    assert user.payload.model_dump(mode="json") == {"kind": "control_user", "username": "carol"}
    assert before == after


def test_access_edits_keep_creation_settings_only_for_remaining_people() -> None:
    original = _intent()
    added = add_access_users_to_intent(original, ["bob"])
    assert added.access.creation_settings == original.access.creation_settings
    assert not remove_access_user_from_intent(added, "carol").access.creation_settings
    with pytest.raises(ValueError, match="unknown users"):
        SetupIntent.model_validate(
            {**original.model_dump(mode="json"), "access": {"users": ["bob"], "creation_settings": {"carol": SETTINGS}}}
        )
