"""Private delivery assignment keeps drafts and confirms the service's exact review."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.control.models import ServiceSettings
from meridian.control.service import ControlService
from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from tests.support.console_dom import needs_console_dom, run_console
from tests.support.control_service import FLEET_ID
from tests.test_delivery_policies import delivery_data

pytestmark = needs_console_dom


@pytest.fixture
def assignment(tmp_path: Path) -> dict:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        user = service.put_user("person")
        state = service.delivery.person("person")
        preview = service.delivery.set_person("person", PersonDeliveryPolicyWrite(policy="full"))
        changed = service.delivery.set_person(
            "person", PersonDeliveryPolicyWrite(policy="full", confirm=True, review_hash=preview.review_hash)
        )
        return {
            "user": user.to_data(),
            "catalog": service.delivery.catalog().to_data(),
            "state": state.to_data(),
            "preview": preview.to_data(),
            "changed": changed.to_data(),
            "afterUser": service.get_user("person").to_data(),
        }
    finally:
        service.close()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_policy_assignment_preserves_drafts_and_submits_one_review(assignment: dict, locale: str) -> None:
    result = run_console("delivery-person", locale, assignment)
    assert "missing" not in result, result
    assert result["dirty"] and result["choiceKept"] and result["focusKept"]
    assert result["previewBody"] == {"policy": "full", "confirm": False}
    assert result["writeBody"] == {
        "policy": "full",
        "confirm": True,
        "review_hash": assignment["preview"]["review_hash"],
    }
    assert result["writes"] == 1 and result["saved"] and not result["dirtyAfter"]
    assert "work" in result["reviewText"] and "full" in result["reviewText"]
    assert "delivery." not in result["reviewText"] and result["stored"] == [] and not result["outside"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_preview_explains_every_format_and_the_destination(tmp_path: Path, locale: str) -> None:
    from tests.test_control_subscription_corpus import FLEETS

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        service.put_user("person")
        service.put_endpoint("entry", FLEETS["fleet"].endpoints[0])
        service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        preview = service.preview_subscription(
            SubscriptionPreviewRequest(username="person", suffix="singbox", destination="chat.example.test:443")
        )
        result = run_console("policy-preview", locale, {"response": preview.to_data()})
        assert result["previewBody"]["destination"] == "chat.example.test:443"
        assert result["formatCount"] == 5 and "chat.example.test" in result["explanation"]
        assert "policy." not in result["text"] and "work" in result["text"] and "base64" in result["text"].lower()
        assert result["stored"] == [] and not result["outside"]
    finally:
        service.close()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_connection_page_limits_stay_local_and_out_of_the_saved_snapshot(tmp_path: Path, locale: str) -> None:
    import json

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        user = service.put_user("person")
        info = json.loads(service.subscription(user.sub_token, "info").body)
        result = run_console("account-delivery-constraints", locale, {"info": info})
        assert "base64" in result["text"] and "deliveryCapabilities" not in result["saved"]
        assert len(result["requests"]) == 1 and all(request["cache"] == "no-store" for request in result["requests"])
    finally:
        service.close()
