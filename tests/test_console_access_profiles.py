"""The person access card and bulk assignment share the service's frozen review in every locale."""

from __future__ import annotations

import pytest

from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from tests.support.console_dom import needs_console_dom, run_console
from tests.test_access_profiles import configure_profiles
from tests.test_access_profiles import service as service

pytestmark = needs_console_dom


@pytest.fixture
def access_case(service):
    profile_service = service
    configure_profiles(profile_service)
    user = profile_service.put_user("person")
    catalog = profile_service.access.catalog()
    before = profile_service.access.person("person")
    review = profile_service.bulk.preview(
        BulkPreviewRequest(usernames=["person"], action=BulkAction(action="set-profiles", profiles=["limited"]))
    )
    receipt = profile_service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    return {
        "user": user.to_data(),
        "catalog": catalog.to_data(),
        "access": before.to_data(),
        "review": review.to_data(),
        "receipt": receipt.to_data(),
        "afterUser": profile_service.get_user("person").to_data(),
        "afterAccess": profile_service.access.person("person").to_data(),
    }


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("surface", ["person", "bulk"])
def test_profile_assignment_reviews_the_exact_loss_and_keeps_private_drafts_in_memory(access_case, locale, surface):
    result = run_console("profiles-" + surface, locale, access_case)
    assert "missing" not in result, result
    assert result["previewRequest"] == {
        "usernames": ["person"],
        "action": {"action": "set-profiles", "profiles": ["limited"]},
    }
    assert result["commitRequest"] == {
        "preview_id": access_case["review"]["id"],
        "preview_hash": access_case["review"]["preview_hash"],
    }
    assert result["commits"] == 1 and result["stored"] == [] and not result["outside"]
    assert "inbound:main:hy2" in result["reviewText"]
    assert "profiles." not in result["reviewText"] and "bulk." not in result["reviewText"]
    if surface == "person":
        assert result["dirty"] and result["refreshKeptChoice"] and result["refreshKeptFocus"]
        assert result["saved"] and not result["dirtyAfterCommit"]
        assert "Connection reality" in result["effective"] and "Connection hysteria2" not in result["effective"]
