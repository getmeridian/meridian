"""Remnawave retirement deletes owned objects idempotently; absent counts as done."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.names import service_username
from meridian.reconciler import remnawave_retirement
from meridian.reconciler.delivery_drivers import ExternalSquadDriver
from meridian.reconciler.remnawave_drivers import (
    ConfigProfileDriver,
    HostDriver,
    InternalSquadDriver,
    NodeBindingDriver,
)
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.routing_drivers import ServiceUserDriver
from meridian.remnawave import RemnawaveNotFoundError
from meridian.remnawave.control_plane import SERVICE_USER_DESCRIPTION

_UUID = "11111111-2222-4333-8444-555555555555"


def _driver(cls, panel: MagicMock):
    return cls(SimpleNamespace(panel=panel))


def _binding(logical_id: str, remote_id: str = _UUID) -> ManagedResourceBinding:
    return ManagedResourceBinding(logical_id=logical_id, resource_kind="k", remote_id=remote_id)


@pytest.mark.parametrize(
    ("cls", "method"),
    [
        (ConfigProfileDriver, "delete_config_profile"),
        (InternalSquadDriver, "delete_internal_squad"),
        (ExternalSquadDriver, "delete_external_squad"),
        (HostDriver, "delete_host"),
    ],
)
def test_owned_objects_are_deleted_by_their_recorded_uuid(cls, method: str) -> None:
    panel = MagicMock()

    _driver(cls, panel).retire(_binding("x"))

    getattr(panel, method).assert_called_once_with(_UUID)


@pytest.mark.parametrize(
    "cls", [ConfigProfileDriver, InternalSquadDriver, ExternalSquadDriver, HostDriver, NodeBindingDriver]
)
def test_never_created_objects_need_no_panel_call(cls) -> None:
    panel = MagicMock()

    _driver(cls, panel).retire(_binding("x", remote_id=""))

    assert panel.method_calls == []


def test_profile_and_squad_deletes_treat_404_as_done() -> None:
    # The adapter's _delete answers False on 404; retirement must not treat that as failure.
    panel = MagicMock()
    panel.delete_config_profile.return_value = False
    panel.delete_internal_squad.return_value = False

    _driver(ConfigProfileDriver, panel).retire(_binding("profile:exit-b"))
    _driver(InternalSquadDriver, panel).retire(_binding("squad:bridge:gw:exit-b"))


def test_host_already_gone_counts_as_retired() -> None:
    panel = MagicMock()
    panel.delete_host.side_effect = RemnawaveNotFoundError("gone")

    _driver(HostDriver, panel).retire(_binding("host:x"))


def test_node_already_gone_counts_as_retired() -> None:
    panel = MagicMock()
    panel.delete_node.side_effect = RemnawaveNotFoundError("gone")

    _driver(NodeBindingDriver, panel).retire(_binding("binding:exit-b"))

    panel.get_node.assert_not_called()


def test_node_retirement_waits_for_the_queued_panel_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = MagicMock()
    panel.get_node.side_effect = [SimpleNamespace(uuid=_UUID), SimpleNamespace(uuid=_UUID), None]
    sleeps: list[float] = []
    monkeypatch.setattr(remnawave_retirement.time, "sleep", sleeps.append)

    _driver(NodeBindingDriver, panel).retire(_binding("binding:exit-b"))

    panel.delete_node.assert_called_once_with(_UUID)
    assert len(sleeps) == 2


def test_node_that_never_disappears_fails_for_a_later_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = MagicMock()
    panel.get_node.return_value = SimpleNamespace(uuid=_UUID)
    clock = iter([0.0, 30.0, 61.0])
    monkeypatch.setattr(remnawave_retirement.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(remnawave_retirement.time, "sleep", lambda _seconds: None)

    with pytest.raises(ResourceReconcileError, match="has not removed it yet"):
        _driver(NodeBindingDriver, panel).retire(_binding("binding:exit-b"))


def test_service_user_is_deleted_by_numeric_id() -> None:
    panel = MagicMock()

    _driver(ServiceUserDriver, panel).retire(_binding("service-user:gw:exit-b", remote_id="42"))

    panel.delete_service_user.assert_called_once_with("42")
    panel.get_user.assert_not_called()


def test_uuid_era_service_user_resolves_its_compiled_username() -> None:
    panel = MagicMock()
    panel.get_user.return_value = SimpleNamespace(id="7", description=SERVICE_USER_DESCRIPTION)

    _driver(ServiceUserDriver, panel).retire(_binding("service-user:gw:exit-b"))

    panel.get_user.assert_called_once_with(service_username("gw:exit-b"))
    panel.delete_service_user.assert_called_once_with("7")


def test_uuid_era_service_user_already_gone_is_done() -> None:
    panel = MagicMock()
    panel.get_user.return_value = None

    _driver(ServiceUserDriver, panel).retire(_binding("service-user:gw:exit-b"))

    panel.delete_service_user.assert_not_called()


def test_a_service_username_taken_over_by_an_unmanaged_user_is_never_deleted() -> None:
    panel = MagicMock()
    panel.get_user.return_value = SimpleNamespace(id="7", description="someone else's account")

    with pytest.raises(ResourceReconcileError, match="refusing to delete"):
        _driver(ServiceUserDriver, panel).retire(_binding("service-user:gw:exit-b"))
    panel.delete_service_user.assert_not_called()
