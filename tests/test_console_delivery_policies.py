"""Private delivery assignment keeps drafts and confirms the service's exact review."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.control.models import ServiceSettings
from meridian.control.service import ControlService
from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
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
