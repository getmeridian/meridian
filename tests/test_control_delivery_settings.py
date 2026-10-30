"""A reviewed refresh interval reaches compiled settings, persistence, and native apps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import urlopen

import pytest
from pydantic import ValidationError

from meridian.cluster import BrandingConfig, ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload, SubscriptionSettingsPayload
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.control.page_settings import save_page_settings
from meridian.control.service import ControlService
from meridian.core.topology import DeliveryIntent
from meridian.reconciler.meridian_drivers import MeridianDriverContext, ServiceSettingsDriver
from meridian.reconciler.resources import build_resource_actions, observation_converges
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.topology import compile_panel_pass, single_exit_intent


@pytest.mark.parametrize("hours", [1, 24, 168])
def test_reviewed_refresh_interval_reaches_both_compiler_settings_payloads(hours: int) -> None:
    intent = single_exit_intent(delivery=DeliveryIntent(update_interval_hours=hours))
    service_plan = compile_topology(intent)
    panel_plan = compile_panel_pass(intent)
    for plan, model in ((service_plan, ServiceSettingsPayload), (panel_plan, SubscriptionSettingsPayload)):
        payload = next(resource.payload for resource in plan.resources if isinstance(resource.payload, model))
        assert payload.update_interval_hours == hours
        assert payload.model_dump(mode="json")["update_interval_hours"] == hours


@pytest.mark.parametrize("model", [DeliveryIntent, ServiceSettingsPayload, ServiceSettings])
def test_default_refresh_interval_is_omitted_from_new_serialized_fields(model: type) -> None:
    fields = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
    implicit = model(**fields)
    explicit = model(**fields, update_interval_hours=6)
    assert implicit.update_interval_hours == explicit.update_interval_hours == 6
    assert implicit.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "update_interval_hours" not in explicit.model_dump(mode="json")


@pytest.mark.parametrize(
    "model", [DeliveryIntent, ServiceSettingsPayload, ServiceSettings, SubscriptionSettingsPayload]
)
@pytest.mark.parametrize("hours", [0, 169, -1, True, 1.5, "6"])
def test_refresh_interval_bounds_and_integer_type_apply_at_every_boundary(model: type, hours: object) -> None:
    fields = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
    with pytest.raises(ValidationError):
        model(**fields, update_interval_hours=hours)


def test_settings_driver_carries_the_reviewed_interval_to_subscription_headers(tmp_path: Path) -> None:
    plan = compile_topology(single_exit_intent(delivery=DeliveryIntent(update_interval_hours=24)))
    action = next(
        action for action in build_resource_actions(plan, 1) if action.resource.logical_id == "settings:service"
    )
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/control", sub_path="connect"))
    context = MagicMock(spec=MeridianDriverContext)
    context.cluster = cluster
    with serve_control(tmp_path) as url:
        context.client = lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False)
        driver = ServiceSettingsDriver(context)
        assert not observation_converges(action, driver.observe(action, None))

        driver.apply(action, None)

        assert observation_converges(action, driver.observe(action, None))
        with ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            settings = client.settings()
            assert settings.update_interval_hours == 24
            assert settings.public_url == "https://198.51.100.10/connect"
            user = client.put_user("person")
        with urlopen(f"{url}/sub/{user.sub_token}", timeout=10) as response:
            assert response.headers["profile-update-interval"] == "24"


def test_refresh_interval_persists_and_can_be_reset_to_the_default(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("person")
    service.put_settings(ServiceSettings(profile_title="Meridian", update_interval_hours=168))
    service.close()
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.settings().update_interval_hours == 168
        assert service.subscription(user.sub_token).headers["profile-update-interval"] == "168"
        service.put_settings(ServiceSettings(profile_title="Meridian"))
        assert service.settings().update_interval_hours == 6
        assert service.subscription(user.sub_token).headers["profile-update-interval"] == "6"
    finally:
        service.close()


def test_page_branding_defaults_are_omitted_and_old_databases_read_them(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        defaults = service.settings()
        assert defaults.server_name == defaults.server_icon == defaults.color == ""
        assert defaults.exit_countries == []
        assert not {"server_name", "server_icon", "color", "exit_countries"} & defaults.model_dump().keys()
    finally:
        service.close()


def test_page_settings_driver_carries_branding_and_exit_countries(tmp_path: Path) -> None:
    intent = single_exit_intent(
        exit_fields={"region": "NL"}, delivery=DeliveryIntent(update_interval_hours=24, client_routing="full")
    )
    plan = compile_topology(intent)
    action = next(
        action for action in build_resource_actions(plan, 1) if action.resource.logical_id == "settings:service"
    )
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", sub_path="connect"),
        branding=BrandingConfig(server_name="Private connection", icon="🛡️", color="forest"),
        topology_intent=intent,
    )
    context = MagicMock(spec=MeridianDriverContext)
    context.cluster = cluster
    with serve_control(tmp_path) as url:
        context.client = lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False)
        driver = ServiceSettingsDriver(context)
        driver.apply(action, None)
        with ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            settings = client.settings()
        assert settings.server_name == "Private connection"
        assert settings.server_icon == "🛡️"
        assert settings.color == "forest"
        assert settings.exit_countries == ["NL"]
        assert settings.update_interval_hours == 24
        assert settings.client_routing == "full"


def test_page_branding_persists_and_can_return_to_defaults(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    service.put_settings(
        ServiceSettings(
            profile_title="Meridian",
            server_name="Private connection",
            color="ocean",
            client_routing="full",
            update_interval_hours=24,
            exit_countries=["NL"],
        )
    )
    service.close()
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.settings().server_name == "Private connection"
        assert service.settings().color == "ocean"
        assert service.settings().client_routing == "full"
        assert service.settings().update_interval_hours == 24
        assert service.settings().exit_countries == ["NL"]
        service.put_settings(ServiceSettings(profile_title="Meridian"))
        assert service.settings().server_name == service.settings().color == ""
        assert service.settings().client_routing == "ru-split"
        assert service.settings().update_interval_hours == 6
        assert service.settings().exit_countries == []
    finally:
        service.close()


def test_page_and_routing_settings_rollback_together(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        before = service.put_settings(ServiceSettings(profile_title="Before", color="ocean"))
        after = ServiceSettings(
            profile_title="After",
            public_url="https://198.51.100.1/connect",
            server_name="Private connection",
            color="forest",
            client_routing="full",
            update_interval_hours=24,
            exit_countries=["NL"],
        )

        def fail_after_page_write(store, settings):
            save_page_settings(store, settings)
            raise RuntimeError("Settings write interrupted")

        with patch("meridian.control.service_settings.save_page_settings", side_effect=fail_after_page_write):
            with pytest.raises(RuntimeError, match="Settings write interrupted"):
                service.put_settings(after)
        assert service.settings() == before
    finally:
        service.close()
