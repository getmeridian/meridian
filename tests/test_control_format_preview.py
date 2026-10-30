"""Format explanations expose selection and omissions without connection secrets."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.compiler import compile_topology
from meridian.control.client import ControlClient, add_mutation_listener
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings, TokenCreate, UserUpdate
from meridian.control.tokens import ROOT
from meridian.core.notices import Notice, NoticeWrite
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from meridian.reconciler.meridian_drivers import ClientEndpointDriver, MeridianDriverContext
from meridian.reconciler.resources import build_resource_actions, observation_converges
from tests.support.topology import single_exit_intent
from tests.test_control_subscription_corpus import FLEETS
from tests.test_control_subscription_formats import DOMAIN_FLEET
from tests.test_control_ui_http import ADMIN_TOKEN, Fleet
from tests.test_control_ui_http import fleet as fleet


def test_preview_explains_selection_without_returning_credentials(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    response = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "Hiddify/2"}, bearer=ADMIN_TOKEN
    )
    assert response.status == 200
    assert response.body["format"] == "singbox"
    assert response.body["selection"]["rule"] == "user_agent"
    assert response.body["selection"]["family"] == "singbox"
    dumped = json.dumps(response.body)
    assert user.uuid not in dumped and user.sub_token not in dumped


def test_disabled_endpoints_are_excluded_from_delivery_and_explained_in_preview(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    endpoint = FLEETS["single"].endpoints[0].model_dump(mode="json")
    response = fleet.call("PUT", "/api/endpoints/disabled", {**endpoint, "enabled": False}, bearer=ADMIN_TOKEN)
    assert response.status == 200
    preview = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "curl"}, bearer=ADMIN_TOKEN
    )
    assert preview.status == 200
    assert preview.body["included"] == []
    assert preview.body["omitted"][0]["reason"] == "disabled_endpoint"
    assert endpoint["reality_public_key"] not in json.dumps(preview.body)
    assert fleet.service.subscription(user.sub_token).body != ""


@pytest.mark.parametrize(
    ("inputs", "format", "rule", "family", "kind"),
    [
        ({"user_agent": "Hiddify/2"}, "singbox", "user_agent", "singbox", "subscription"),
        ({"user_agent": "clash for android"}, "mihomo", "user_agent", "clash", "subscription"),
        ({"user_agent": "Happ/1"}, "xray-json", "user_agent", "xray-json", "subscription"),
        ({"user_agent": "Stash/1"}, "stash", "user_agent", "stash", "subscription"),
        ({"user_agent": "Unknown"}, "base64", "default", None, "subscription"),
        ({"user_agent": "Hiddify/2", "accept": "text/html"}, "browser", "accept", None, "page"),
        (
            {"user_agent": "Hiddify/2", "accept": "text/html", "suffix": "stash"},
            "stash",
            "suffix",
            None,
            "subscription",
        ),
        ({"suffix": "info"}, "browser", "suffix", None, "account_info"),
    ],
)
def test_preview_explains_the_same_first_matching_rule_as_live_delivery(
    fleet: Fleet, inputs: dict, format: str, rule: str, family: str | None, kind: str
) -> None:
    fleet.service.put_user("person")
    reply = fleet.call("POST", "/api/subscription/preview", {"username": "person", **inputs}, bearer=ADMIN_TOKEN)
    assert reply.status == 200
    assert (
        reply.body["format"],
        reply.body["selection"]["rule"],
        reply.body["selection"]["family"],
        reply.body["response_kind"],
    ) == (format, rule, family, kind)
    assert "user_agent" not in reply.body and "accept" not in reply.body


def test_preview_uses_native_protocol_and_copy_filters(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    for index, endpoint in enumerate(DOMAIN_FLEET.endpoints):
        fleet.service.put_endpoint(f"entry-{index}", endpoint)
    reply = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "Hiddify/2"}, bearer=ADMIN_TOKEN
    )
    assert reply.status == 200
    assert reply.body["protocols"] == ["reality", "wss", "hysteria2"]
    assert all(entry["reason"] == "unsupported_format" for entry in reply.body["omitted"])
    restrictions = {(entry["protocol"], entry["restriction"]) for entry in reply.body["omitted"]}
    assert ("xhttp", "protocol") in restrictions and ("reality", "fingerprint_copy") in restrictions
    assert len(reply.body["included"]) + len(reply.body["omitted"]) == len(DOMAIN_FLEET.endpoints)


@pytest.mark.parametrize("status", ["disabled", "expired"])
def test_preview_explains_account_placeholders_before_format_restrictions(fleet: Fleet, status: str) -> None:
    fleet.service.put_user("person")
    fleet.service.put_endpoint(
        "xhttp", next(endpoint for endpoint in DOMAIN_FLEET.endpoints if endpoint.protocol == "xhttp")
    )
    fleet.service.update_user(
        "person",
        UserUpdate(status="disabled") if status == "disabled" else UserUpdate(expire_at="2020-01-01T00:00:00Z"),
    )
    reply = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "Hiddify/2"}, bearer=ADMIN_TOKEN
    )
    assert reply.body["status"] == reply.body["placeholder"] == status
    assert reply.body["protocols"] == [] and reply.body["included"] == []
    assert reply.body["omitted"][0]["reason"] == "status_placeholder"


def test_preview_explains_copy_only_availability_placeholders(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    fleet.service.put_endpoint(
        "copy",
        next(
            endpoint
            for endpoint in DOMAIN_FLEET.endpoints
            if endpoint.fingerprint_copy and endpoint.protocol == "reality"
        ),
    )
    reply = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "Happ/1"}, bearer=ADMIN_TOKEN
    )
    assert reply.body["placeholder"] == "no_connections"
    assert reply.body["included"] == [] and reply.body["omitted"][0]["reason"] == "availability_placeholder"


@pytest.mark.parametrize("level", ["off", "private", "full"])
def test_preview_is_read_scoped_and_never_registers_devices_or_changes_storage(
    fleet: Fleet, level: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet.service.put_user("person")
    fleet.service.set_privacy(PrivacySettings(device_limits=level), author="root")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a preview must never register a device")

    monkeypatch.setattr(fleet.service.devices, "fetch", refuse)
    before = fleet.service._db.total_changes
    transient = "Hiddify/2 TRANSIENT_DEVICE_INPUT"
    reply = fleet.call(
        "POST",
        "/api/subscription/preview",
        {"username": "person", "user_agent": transient, "accept": "application/json"},
        bearer=ADMIN_TOKEN,
    )
    assert reply.status == 200 and fleet.service._db.total_changes == before
    assert transient not in json.dumps(reply.body)
    token = fleet.service.tokens.issue(TokenCreate(name="reader", scope="read"), issuer=ROOT)
    assert fleet.call("POST", "/api/subscription/preview", {"username": "person"}, bearer=token.token).status == 200


def test_preview_redacts_uris_keys_private_metadata_and_encoded_headers(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    endpoint = DOMAIN_FLEET.endpoints[0]
    fleet.service.put_endpoint(
        "reality", endpoint.model_copy(update={"remark": "vless://hidden@example.invalid?pbk=other-key"})
    )
    fleet.service.update_user("person", UserUpdate(note="Private note", tag="PRIVATE"))
    fleet.service.put_settings(
        ServiceSettings(profile_title="Title " + user.uuid, public_url="https://198.51.100.1/connect")
    )
    fleet.service.notices.update(NoticeWrite(notice=Notice(text="Sensitive announcement"), confirm=True))
    reply = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": "Happ/1"}, bearer=ADMIN_TOKEN
    )
    dumped = json.dumps(reply.body)
    assert all(
        value not in dumped
        for value in (
            user.uuid,
            user.sub_token,
            endpoint.reality_public_key,
            endpoint.reality_short_id,
            "Private note",
            "PRIVATE",
            "Sensitive announcement",
            "vless://",
            "https://",
        )
    )
    assert reply.body["included"][0]["remark"] == "[redacted]"
    assert reply.body["response_headers"]["announce"] == "base64:[redacted]"
    assert reply.body["response_headers"]["profile-web-page-url"] == "[redacted]"


@pytest.mark.parametrize(
    "inputs",
    [
        {"user_agent": "x" * 513},
        {"accept": "x" * 257},
        {"user_agent": "a\nb"},
        {"accept": "text/html\r\nInjected"},
        {"suffix": "other"},
        {"device_ip": "198.51.100.2"},
    ],
)
def test_preview_refuses_unbounded_or_device_inputs_before_work(fleet: Fleet, inputs: dict) -> None:
    fleet.service.put_user("person")
    before = fleet.service._db.total_changes
    reply = fleet.call("POST", "/api/subscription/preview", {"username": "person", **inputs}, bearer=ADMIN_TOKEN)
    assert reply.status == 400 and fleet.service._db.total_changes == before


def test_preview_is_read_only_for_backup_mutation_hooks(fleet: Fleet) -> None:
    fleet.service.put_user("person")
    calls: list[str] = []
    stop = add_mutation_listener(calls.append)
    try:
        with ControlClient(fleet.url, ADMIN_TOKEN) as client:
            assert (
                client.preview_subscription(
                    SubscriptionPreviewRequest(username="person", user_agent="Hiddify/2")
                ).format
                == "singbox"
            )
        assert calls == []
    finally:
        stop()


def test_reviewed_endpoint_apply_restores_a_disabled_endpoint(fleet: Fleet) -> None:
    plan = compile_topology(single_exit_intent())
    action = next(
        action
        for action in build_resource_actions(plan, 1)
        if action.resource.payload.kind == "client_endpoint" and action.resource.payload.protocol == "reality"
    )
    context = MagicMock(spec=MeridianDriverContext)
    context.client = lambda: ControlClient(fleet.url, ADMIN_TOKEN, report_mutations=False)
    context.reality_keys.return_value = SimpleNamespace(
        public_key=DOMAIN_FLEET.endpoints[0].reality_public_key, short_id="a1a1a1a1"
    )
    context.address.return_value = "198.51.100.1"
    driver = ClientEndpointDriver(context)
    receipt = driver.apply(action, None)
    endpoint = fleet.service.get_endpoint(receipt.remote_id)
    assert "enabled" not in endpoint.model_dump()
    fleet.service.put_endpoint(
        receipt.remote_id, ClientEndpoint.model_validate({**endpoint.model_dump(), "enabled": False})
    )
    assert not observation_converges(action, driver.observe(action, None))
    driver.apply(action, None)
    assert fleet.service.get_endpoint(receipt.remote_id).enabled is True
    assert observation_converges(action, driver.observe(action, None))
