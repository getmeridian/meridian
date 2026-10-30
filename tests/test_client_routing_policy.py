"""A reviewed client policy reaches every structured subscription without changing defaults."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import ValidationError

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload, SubscriptionSettingsPayload, SubscriptionTemplatePayload
from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.control.service import ControlService
from meridian.core.topology import DeliveryIntent
from meridian.reconciler.meridian_drivers import MeridianDriverContext, ServiceSettingsDriver
from meridian.reconciler.resources import build_resource_actions, observation_converges
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.topology import compile_panel_pass, single_exit_intent
from tests.test_control_subscription_corpus import FLEETS


@pytest.mark.parametrize(
    "model", [DeliveryIntent, SubscriptionSettingsPayload, ServiceSettingsPayload, ServiceSettings]
)
def test_default_client_policy_is_omitted_at_every_serialized_boundary(model: type) -> None:
    fields = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
    implicit = model(**fields)
    explicit = model(**fields, client_routing="ru-split")
    assert implicit.client_routing == explicit.client_routing == "ru-split"
    assert implicit.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "client_routing" not in explicit.model_dump(mode="json")


@pytest.mark.parametrize(
    "model", [DeliveryIntent, SubscriptionSettingsPayload, ServiceSettingsPayload, ServiceSettings]
)
@pytest.mark.parametrize("policy", ["ir-split", "cn-split", "other", "", True, None])
def test_only_the_two_reviewed_policies_are_accepted(model: type, policy: object) -> None:
    fields = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
    with pytest.raises(ValidationError):
        model(**fields, client_routing=policy)


def test_full_policy_reaches_both_settings_payloads_and_every_compiled_template() -> None:
    intent = single_exit_intent(delivery=DeliveryIntent(client_routing="full"))
    for plan, model in (
        (compile_topology(intent), ServiceSettingsPayload),
        (compile_panel_pass(intent), SubscriptionSettingsPayload),
    ):
        payload = next(resource.payload for resource in plan.resources if isinstance(resource.payload, model))
        assert payload.client_routing == "full"
        assert payload.model_dump(mode="json")["client_routing"] == "full"

    templates = [
        resource.payload
        for resource in compile_panel_pass(intent).resources
        if isinstance(resource.payload, SubscriptionTemplatePayload)
    ]
    assert {template.template_type for template in templates} == {"XRAY_JSON", "MIHOMO", "SINGBOX"}
    for template in templates:
        rendered = json.dumps(template.template_json) if template.template_json else template.template_yaml
        assert "private" in rendered
        assert not any(matcher in rendered for matcher in ("category-ru", "geoip:ru", "GEOIP,RU", "DOMAIN-SUFFIX,ru"))
        if template.template_type == "XRAY_JSON":
            assert template.template_json is not None
            rules = template.template_json["routing"]["rules"]
            assert {"type": "field", "protocol": ["bittorrent"], "outboundTag": "DIRECT"} in rules[:-1]
            assert rules[-1]["balancerTag"] == "MERIDIAN_AUTO"


def test_settings_driver_applies_the_reviewed_policy_and_detects_a_later_change(tmp_path: Path) -> None:
    plan = compile_topology(
        single_exit_intent(delivery=DeliveryIntent(client_routing="full", update_interval_hours=24))
    )
    action = next(
        action for action in build_resource_actions(plan, 1) if action.resource.logical_id == "settings:service"
    )
    context = MagicMock(spec=MeridianDriverContext)
    context.cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/control", sub_path="connect"))
    with serve_control(tmp_path) as url:
        context.client = lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False)
        driver = ServiceSettingsDriver(context)
        assert not observation_converges(action, driver.observe(action, None))
        driver.apply(action, None)
        assert observation_converges(action, driver.observe(action, None))
        with context.client() as client:
            settings = client.settings()
            assert settings.client_routing == "full"
            assert settings.update_interval_hours == 24
            assert settings.public_url == "https://198.51.100.10/connect"
            client.put_settings(ServiceSettings(profile_title="Meridian"))
        assert not observation_converges(action, driver.observe(action, None))


@pytest.fixture
def service(tmp_path: Path):
    control = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    for index, endpoint in enumerate(FLEETS["fleet"].endpoints):
        control.put_endpoint(f"endpoint-{index}", endpoint)
    yield control
    control.close()


@pytest.mark.parametrize("policy", ["ru-split", "full"])
@pytest.mark.parametrize("document", ["json", "mihomo", "stash", "singbox"])
def test_service_subscriptions_follow_the_policy_in_every_automatic_group(
    service: ControlService, policy: str, document: str
) -> None:
    user = service.put_user("person")
    service.put_settings(ServiceSettings(profile_title="Meridian", client_routing=policy))
    response = service.subscription(user.sub_token, document)
    split = policy == "ru-split"
    if document == "json":
        configs = json.loads(response.body)
        assert len(configs) > 1  # Every fingerprint's automatic group must use the same policy.
        for config in configs:
            rules = config["routing"]["rules"]
            assert {"type": "field", "ip": ["geoip:private"], "outboundTag": "DIRECT"} in rules
            assert ({"type": "field", "ip": ["geoip:ru"], "outboundTag": "DIRECT"} in rules) == split
            assert any("domain:ru" in rule.get("domain", []) for rule in rules) == split
            assert {"type": "field", "protocol": ["bittorrent"], "outboundTag": "DIRECT"} in rules[:-1]
            assert config["routing"]["balancers"][0]["fallbackTag"] == "BLOCK"
            assert rules[-1] == {"type": "field", "network": "tcp,udp", "balancerTag": "MERIDIAN_AUTO"}
    elif document in ("mihomo", "stash"):
        rules = yaml.safe_load(response.body)["rules"]
        private = "LAN" if document == "stash" else "private"
        assert f"GEOIP,{private},DIRECT,no-resolve" in rules
        assert ("DOMAIN-SUFFIX,ru,DIRECT" in rules) == split
        assert ("GEOIP,RU,DIRECT,no-resolve" in rules) == split
        assert rules[-1] == "MATCH,Meridian"
    else:
        route = json.loads(response.body)["route"]
        assert {"ip_is_private": True, "outbound": "direct"} in route["rules"]
        assert ({"domain_suffix": [".ru", ".su", ".xn--p1ai"], "outbound": "direct"} in route["rules"]) == split
        assert "rule_set" not in route  # No external geo-list downloads.
        assert route["final"] == "Meridian"


def test_base64_and_browser_links_do_not_invent_routing_syntax(service: ControlService) -> None:
    user = service.put_user("person")
    before = {document: service.subscription(user.sub_token, document) for document in ("", "info")}
    service.put_settings(ServiceSettings(profile_title="Meridian", client_routing="full"))
    for document, original in before.items():
        assert service.subscription(user.sub_token, document) == original
    links = base64.b64decode(before[""].body).decode().splitlines()
    assert links and all(link.startswith(("vless://", "hysteria2://")) for link in links)


def test_old_databases_default_to_ru_split_and_full_persists_until_reset(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    service.close()
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM meta WHERE key = 'client_routing'")
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    assert service.settings().client_routing == "ru-split"
    service.put_settings(ServiceSettings(profile_title="Meridian", client_routing="full"))
    service.close()
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.settings().client_routing == "full"
        service.put_settings(ServiceSettings(profile_title="Meridian"))
        assert service.settings().client_routing == "ru-split"
    finally:
        service.close()


def test_a_failed_policy_write_rolls_back_the_other_delivery_settings(
    service: ControlService, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = service.settings()
    write_meta = service._store.set_meta

    def fail_policy(key: str, value: str) -> None:
        if key == "client_routing":
            raise sqlite3.OperationalError("write failed")
        write_meta(key, value)

    monkeypatch.setattr(service._store, "set_meta", fail_policy)
    with pytest.raises(sqlite3.OperationalError, match="write failed"):
        service.put_settings(
            ServiceSettings(
                profile_title="Ours",
                public_url="https://198.51.100.10/connect",
                update_interval_hours=24,
                client_routing="full",
            )
        )
    assert service.settings() == before
