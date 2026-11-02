"""Reviewed app rules reach delivery without changing the built-in corpus tail."""

from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload
from meridian.control.client import ControlClient
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.response_rules import evaluate_response_rules
from meridian.control.formats.selector import explain_format
from meridian.control.models import ServiceSettings
from meridian.control.service import ControlService
from meridian.control.subscriptions import apply_response_headers, render_subscription
from meridian.core.delivery_policies import resolve_delivery_policy
from meridian.core.fleet_document import intent_changes
from meridian.core.response_rule_test import ResponseRuleTestRequest
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from meridian.core.topology import DeliveryIntent
from meridian.reconciler.meridian_drivers import MeridianDriverContext, ServiceSettingsDriver
from meridian.reconciler.resources import build_resource_actions, observation_converges
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.token_service import serve_tokens, token_request
from tests.support.topology import single_exit_intent
from tests.test_control_subscription_corpus import CORPUS, FLEETS, NOW, SHORT_ID, UUID


def app_rule(action: dict, *, name: str = "happ", value: str = "Happ/", operator: str = "prefix") -> dict:
    return {"name": name, "match": {"user_agent": {"operator": operator, "value": value}}, "action": action}


def test_reviewed_response_rules_compile_into_service_settings() -> None:
    rules = [app_rule({"kind": "format", "format": "singbox"})]
    delivery = DeliveryIntent.model_validate({"response_rules": rules})
    plan = compile_topology(single_exit_intent(delivery=delivery))
    settings = next(item.payload for item in plan.resources if isinstance(item.payload, ServiceSettingsPayload))
    assert settings.to_data()["response_rules"] == rules


@pytest.mark.parametrize("kind", ["format", "block", "policy", "headers"])
def test_custom_actions_reach_live_delivery(tmp_path: Path, kind: str) -> None:
    actions = {
        "format": {"kind": "format", "format": "singbox"},
        "block": {"kind": "block"},
        "policy": {"kind": "policy", "policy": "full"},
        "headers": {
            "kind": "headers",
            "headers": {
                "profile_title": "Private connection",
                "update_interval_hours": 24,
                "support_url": "https://support.example.test/help",
                "announce": "Refresh your connections",
            },
        },
    }
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = service.put_user("person")
        for number, endpoint in enumerate(FLEETS["fleet"].endpoints):
            service.put_endpoint(str(number), endpoint)
        service.put_settings(
            ServiceSettings.model_validate({"profile_title": "Meridian", "response_rules": [app_rule(actions[kind])]})
        )
        response = service.subscription(person.sub_token, user_agent="Happ/3.2.1")
        if kind == "format":
            assert "outbounds" in json.loads(response.body)
        elif kind == "block":
            assert response == service.subscription("unknown", user_agent="Happ/3.2.1")
            assert response.status == 404
        elif kind == "policy":
            assert all("geosite:category-ru" not in json.dumps(config) for config in json.loads(response.body))
        else:
            assert base64.b64decode(response.headers["profile-title"][7:]).decode() == "Private connection"
            assert response.headers["profile-update-interval"] == "24"
            assert response.headers["support-url"] == "https://support.example.test/help"
            assert base64.b64decode(response.headers["announce"][7:]).decode() == "Refresh your connections"
        assert (
            service.subscription(person.sub_token, user_agent="Stash/3.2.0").headers["profile-update-interval"] == "6"
        )
    finally:
        service.close()


def test_matcher_api_explains_builtin_selection_without_a_person(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url:
        status, result = token_request(
            url + "/api/response-rules/test", "POST", {"user_agent": "Hiddify/2"}, ADMIN_TOKEN
        )
    assert status == 200, result
    assert result["format"] == "singbox"
    assert result["selection"]["rule"] == "user_agent"
    assert result["reason"] == "built_in_selector"
    assert "Hiddify/2" not in json.dumps(result)


@pytest.mark.parametrize("key", sorted(CORPUS), ids=lambda key: "/".join(key))
def test_empty_rules_keep_every_golden_request_and_delivered_byte(key: tuple[str, str]) -> None:
    capture = CORPUS[key]
    request, fleet = capture.request, FLEETS[capture.topology]
    settings = ServiceSettings(profile_title=fleet.title, public_url=fleet.public_url)
    original = explain_format(request["user_agent"], request["accept"], request["selector"])
    decision = evaluate_response_rules(
        settings, user_agent=request["user_agent"], accept=request["accept"], suffix=request["selector"]
    )
    assert decision.selection == original.selection and decision.format == original.format
    assert not decision.block and decision.headers == {}
    user = SubscriptionUser(
        username=fleet.username,
        uuid=UUID,
        short_id=SHORT_ID,
        status="disabled" if request["user"] == "disabled" else "active",
    )
    kwargs = {"profile_title": fleet.title, "public_url": fleet.public_url, "now": NOW}
    before = render_subscription(user, fleet.endpoints, original.format, **kwargs)
    after = render_subscription(user, fleet.endpoints, decision.format, delivery_policy=decision.policy, **kwargs)
    assert apply_response_headers(after, decision.headers) == before


@pytest.mark.parametrize("model", [DeliveryIntent, ServiceSettingsPayload, ServiceSettings])
def test_empty_rule_lists_stay_off_existing_intent_and_payload_hashes(model: type) -> None:
    values = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
    assert model(**values).to_data() == model(**values, response_rules=[]).to_data()
    assert "response_rules" not in model(**values).to_data()


@pytest.mark.parametrize(
    "operator,value,agent,expected",
    [
        ("equals", "Happ/3", "hAPP/3", True),
        ("equals", "Happ/3", "Happ/30", False),
        ("prefix", "Happ/", "happ/3", True),
        ("prefix", "Happ/", "Other Happ/3", False),
        ("contains", "happ/", "Other HAPP/3", True),
        ("contains", "^Happ.*", "Happ/3", False),
    ],
)
def test_matchers_are_case_insensitive_literals(operator: str, value: str, agent: str, expected: bool) -> None:
    settings = ServiceSettings.model_validate(
        {
            "profile_title": "Meridian",
            "response_rules": [app_rule({"kind": "format", "format": "stash"}, operator=operator, value=value)],
        }
    )
    result = evaluate_response_rules(settings, user_agent=agent)
    assert (result.selection.rule == "custom") == expected
    assert agent not in json.dumps(result.to_data())


def test_accept_narrows_a_rule_and_order_is_preserved_in_fleet_reviews() -> None:
    first = app_rule({"kind": "format", "format": "singbox"}, name="json-app")
    first["match"]["accept"] = {"operator": "contains", "value": "APPLICATION/JSON"}
    second = app_rule({"kind": "format", "format": "stash"}, name="yaml-app")
    settings = ServiceSettings.model_validate({"profile_title": "Meridian", "response_rules": [first, second]})
    assert evaluate_response_rules(settings, user_agent="Happ/3", accept="application/json").format == "singbox"
    assert evaluate_response_rules(settings, user_agent="Happ/3", accept="*/*").selection.name == "yaml-app"
    before = single_exit_intent(delivery=DeliveryIntent.model_validate({"response_rules": [first]}))
    after = single_exit_intent(delivery=DeliveryIntent.model_validate({"response_rules": [first, second]}))
    assert any(
        change.subject == "delivery" and change.field == "response_rules" for change in intent_changes(before, after)
    )


@pytest.mark.parametrize("suffix", ["", "info", "json", "mihomo", "stash", "singbox"])
def test_custom_format_and_block_precede_suffix_and_browser_accept(suffix: str) -> None:
    for action in ({"kind": "format", "format": "singbox"}, {"kind": "block"}):
        settings = ServiceSettings.model_validate({"profile_title": "Meridian", "response_rules": [app_rule(action)]})
        decision = evaluate_response_rules(settings, user_agent="Happ/3", accept="text/html", suffix=suffix)
        assert decision.selection.name == "happ"
        assert decision.format == (None if action["kind"] == "block" else "singbox")
        assert decision.block == (action["kind"] == "block")


@pytest.mark.parametrize(
    "person,app,profiles,expected,source",
    [
        ("ru-split", "work", ["family"], "ru-split", "person"),
        (None, "work", ["family"], "work", "response_rule"),
        (None, None, ["family"], "ru-split", "profile"),
        (None, None, [], "full", "fleet"),
    ],
)
def test_policy_precedence_has_one_resolver(
    person: str | None, app: str | None, profiles: list[str], expected: str, source: str
) -> None:
    settings = ServiceSettings.model_validate(
        {
            "profile_title": "Meridian",
            "default_policy": "full",
            "delivery_policies": [{"id": "work"}],
            "profile_policies": {"family": "ru-split"},
        }
    )
    decision = resolve_delivery_policy(settings, person_override=person, response_override=app, profiles=profiles)
    assert (decision.policy.id, decision.source) == (expected, source)


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "x" * 49},
        {"match": {"user_agent": {"operator": "regex", "value": ".*"}}},
        {"match": {"user_agent": {"operator": "prefix", "value": ""}}},
        {"match": {"user_agent": {"operator": "contains", "value": "x" * 257}}},
        {"match": {"user_agent": {"operator": "equals", "value": "Happ/3\r\nHeader: injected"}}},
        {
            "match": {
                "user_agent": {"operator": "prefix", "value": "Happ/"},
                "x-forwarded-for": {"operator": "equals", "value": "198.51.100.1"},
            }
        },
        {"action": {"kind": "format", "format": "remote-yaml"}},
        {"action": {"kind": "policy", "policy": "missing"}},
        {"action": {"kind": "block", "headers": {"profile_title": "Rejected"}}},
        {"action": {"kind": "headers", "headers": {}}},
        {"action": {"kind": "headers", "headers": {"x-custom-header": "value"}}},
        {"action": {"kind": "headers", "headers": {"profile_title": "Two\nlines"}}},
        {"action": {"kind": "headers", "headers": {"announce": "x" * 501}}},
        {"action": {"kind": "headers", "headers": {"announce": "Hidden\u202econtrol"}}},
        {"action": {"kind": "headers", "headers": {"update_interval_hours": True}}},
        {"action": {"kind": "headers", "headers": {"update_interval_hours": 169}}},
        {"action": {"kind": "headers", "headers": {"support_url": "https://support.example.test/%0d%0ainjected"}}},
        {"action": {"kind": "headers", "headers": {"support_url": "javascript:alert(1)"}}},
    ],
)
def test_unbounded_or_untyped_rule_inputs_refuse_at_intent_and_service_boundaries(bad: dict) -> None:
    rule = {**app_rule({"kind": "format", "format": "stash"}), **bad}
    for model in (DeliveryIntent, ServiceSettings):
        values = {} if model is DeliveryIntent else {"profile_title": "Meridian"}
        with pytest.raises(ValidationError):
            model.model_validate({**values, "response_rules": [rule]})


def test_duplicate_names_and_provably_shadowed_rules_refuse_before_review() -> None:
    broad = app_rule({"kind": "block"}, name="broad")
    narrow = app_rule({"kind": "format", "format": "stash"}, name="narrow", operator="equals", value="happ/3")
    for rules in ([broad, broad], [broad, narrow]):
        with pytest.raises(ValidationError, match="unique|unreachable"):
            DeliveryIntent.model_validate({"response_rules": rules})
    assert len(DeliveryIntent.model_validate({"response_rules": [narrow, broad]}).response_rules) == 2
    narrow["name"] = "broad"
    with pytest.raises(ValidationError, match="unique"):
        DeliveryIntent.model_validate({"response_rules": [narrow, broad]})


def test_rule_count_and_total_encoded_catalog_size_are_bounded() -> None:
    rules = [
        app_rule({"kind": "headers", "headers": {"announce": "پیام" * 120}}, name=f"app-{index}", value=f"App-{index}/")
        for index in range(65)
    ]
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate({"response_rules": rules})
    with pytest.raises(ValidationError, match="64 KiB"):
        DeliveryIntent.model_validate({"response_rules": rules[:64]})


def test_service_settings_driver_observes_and_preserves_reviewed_response_rules(tmp_path: Path) -> None:
    intent = single_exit_intent(
        delivery=DeliveryIntent.model_validate({"response_rules": [app_rule({"kind": "block"})]})
    )
    action = next(
        action
        for action in build_resource_actions(compile_topology(intent), 1)
        if action.resource.logical_id == "settings:service"
    )
    context = MagicMock(spec=MeridianDriverContext)
    context.access_review_hash = None
    context.cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/control", sub_path="connect"))
    with serve_control(tmp_path) as url:
        context.client = lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False)
        driver = ServiceSettingsDriver(context)
        assert not observation_converges(action, driver.observe(action, None))
        driver.apply(action, None)
        assert observation_converges(action, driver.observe(action, None))
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.settings().response_rules == intent.delivery.response_rules
        service.put_settings(ServiceSettings(profile_title="Meridian"))
        assert service.settings().response_rules == []
    finally:
        service.close()


def test_api_tester_has_a_narrow_grant_and_private_person_preconditions(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        person = fleet.service.put_user("person")
        status, issued = token_request(
            fleet.url + "/api/tokens", "POST", {"name": "Tester", "capabilities": ["delivery.test"]}, ADMIN_TOKEN
        )
        assert status == 200
        for body, expected in (({"user_agent": "Happ/3"}, 200), ({"user_agent": "Happ/3", "person": "person"}, 403)):
            status, result = token_request(fleet.url + "/api/response-rules/test", "POST", body, issued["token"])
            assert status == expected, result
            assert person.uuid not in json.dumps(result) and person.sub_token not in json.dumps(result)
        assert token_request(fleet.url + "/api/settings", token=issued["token"])[0] == 403
        assert (
            token_request(fleet.url + "/api/response-rules/test", "POST", {"person": "missing"}, ADMIN_TOKEN)[0] == 404
        )


def test_rule_delivery_and_previews_never_store_request_headers_or_register_blocked_devices(tmp_path: Path) -> None:
    from meridian.control.models import PrivacySettings

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = service.put_user("person")
        service.set_privacy(PrivacySettings(device_limits="full"), author="operator")
        service.put_settings(
            ServiceSettings.model_validate(
                {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "block"})]}
            )
        )
        writes, events = service._db.total_changes, service.events.list()
        agent = "Happ/3 private-request-marker"
        decision = service.test_response_rules(ResponseRuleTestRequest(user_agent=agent, person="person"))
        preview = service.preview_subscription(SubscriptionPreviewRequest(username="person", user_agent=agent))
        response = service.subscription(
            person.sub_token,
            user_agent=agent,
            device_headers={"x-hwid": "test-device", "x-device-model": "private-device-marker"},
        )
        assert decision.block and preview.response_kind == "blocked" and preview.included == []
        assert response == service.subscription("missing", user_agent=agent)
        assert service._db.total_changes == writes and service.events.list() == events
        dump = "\n".join(service._db.iterdump())
        assert "private-request-marker" not in dump and "private-device-marker" not in dump
        assert "private-request-marker" not in json.dumps(preview.to_data())
    finally:
        service.close()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "suffix", ["", "/info", "/json", "/index.html", "/support.html", "/config.json", "/manifest.webmanifest"]
)
def test_blocked_apps_cannot_distinguish_a_valid_token_on_any_personal_route(
    tmp_path: Path, method: str, suffix: str, legacy: bool
) -> None:
    with serve_tokens(tmp_path) as fleet:
        person = fleet.service.put_user("person")
        fleet.service.put_settings(
            ServiceSettings.model_validate(
                {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "block"})]}
            )
        )
        replies = []
        origin = urlsplit(fleet.url)
        identities = (
            (person.uuid, "00000000-0000-4000-8000-000000000000") if legacy else (person.sub_token, "unknown-token")
        )
        for identity in identities:
            connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=10)
            try:
                connection.request(
                    method,
                    ("/sub/~legacy/" if legacy else "/sub/") + identity + suffix,
                    headers={"User-Agent": "Happ/3", "Accept": "text/html"},
                )
                response = connection.getresponse()
                replies.append(
                    (
                        response.status,
                        response.read(),
                        {name.lower(): value for name, value in response.getheaders() if name.lower() != "date"},
                    )
                )
            finally:
                connection.close()
        assert replies[0] == replies[1] and replies[0][0] == 404


def test_blocked_delivery_refuses_before_reading_a_person(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = service.put_user("person")
        service.put_settings(
            ServiceSettings.model_validate(
                {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "block"})]}
            )
        )
        statements: list[str] = []
        service._db.set_trace_callback(statements.append)
        assert service.subscription(person.sub_token, user_agent="Happ/3").status == 404
        assert not any("from users" in statement.lower() for statement in statements)
    finally:
        service.close()


def test_combined_action_uses_person_policy_and_app_headers_without_changing_saved_choices(tmp_path: Path) -> None:
    from meridian.core.notices import Notice, NoticeWrite
    from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = service.put_user("person")
        service.put_endpoint("main", FLEETS["fleet"].endpoints[0])
        service.notices.update(NoticeWrite(notice=Notice(text="Default announcement"), confirm=True))
        settings = ServiceSettings.model_validate(
            {
                "profile_title": "Meridian",
                "public_url": "https://198.51.100.10/connect",
                "response_rules": [
                    app_rule(
                        {
                            "kind": "format",
                            "format": "mihomo",
                            "policy": "full",
                            "headers": {"profile_title": "پیام‌های برنامه", "announce": "", "support_url": ""},
                        }
                    )
                ],
            }
        )
        service.put_settings(settings)
        review = service.delivery.set_person("person", PersonDeliveryPolicyWrite(policy="ru-split"))
        service.delivery.set_person(
            "person", PersonDeliveryPolicyWrite(policy="ru-split", confirm=True, review_hash=review.review_hash)
        )
        response = service.subscription(person.sub_token, user_agent="Happ/3")
        assert response.content_type.startswith("text/yaml")
        assert "announce" not in response.headers and "support-url" not in response.headers
        assert base64.b64decode(response.headers["profile-title"][7:]).decode() == "پیام‌های برنامه"
        assert "GEOSITE,category-ru,DIRECT" in response.body
        preview = service.preview_subscription(SubscriptionPreviewRequest(username="person", user_agent="Happ/3"))
        decision = service.test_response_rules(ResponseRuleTestRequest(user_agent="Happ/3", person="person"))
        assert preview.delivery.effective == decision.policy
        assert decision.policy.source == "person" and decision.policy.policy.id == "ru-split"
        assert decision.headers["profile-title"] == "base64:[redacted]"
        assert decision.headers["support-url"] == decision.headers["announce"] == ""
        assert service.get_user("person").delivery_policy == "ru-split" and service.settings() == settings
    finally:
        service.close()


def test_encrypted_restore_preserves_rules_and_no_request_history(tmp_path: Path) -> None:
    from meridian.control.models import DeviceKeyCreate
    from meridian.control.restore import stage_restored_database
    from meridian.control.schema import SCHEMA_VERSION
    from meridian.control.snapshot import decrypt_snapshot, generate_key_pair

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        key = generate_key_pair()
        service.backups.register_device(DeviceKeyCreate(name="operator", public_key=key.public))
        settings = ServiceSettings.model_validate(
            {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "block"})]}
        )
        service.put_settings(settings)
        service.test_response_rules(ResponseRuleTestRequest(user_agent="Happ/3 request-history-marker"))
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
        assert b"request-history-marker" not in snapshot.database
        restored_path = tmp_path / "restored.db"
        stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0")
    finally:
        service.close()
    restored = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.settings() == settings
        assert restored._store.meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        restored.close()


def test_matcher_headers_stay_transient_beside_disclosed_device_details(tmp_path: Path) -> None:
    """Full device mode keeps its disclosed User-Agent; rule matching itself stores no request header."""
    from meridian.control.models import PrivacySettings

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = service.put_user("person")
        service.set_privacy(PrivacySettings(device_limits="full"), author="operator")
        service.put_settings(
            ServiceSettings.model_validate(
                {"profile_title": "Meridian", "response_rules": [app_rule({"kind": "policy", "policy": "full"})]}
            )
        )
        marker = "text/plain; transient-accept-marker"
        service.subscription(
            person.sub_token, user_agent="Happ/3", accept=marker, device_headers={"x-hwid": "device-12345"}
        )
        assert "transient-accept-marker" not in "\n".join(service._db.iterdump())
    finally:
        service.close()
