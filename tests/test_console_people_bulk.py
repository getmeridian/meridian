"""Keyboard selection, all-matching review, receipts, and undo in every locale."""

import pytest

from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from meridian.core.people_query import PeopleQuery
from tests.support.console_dom import needs_console_dom, run_console
from tests.test_control_people_query import people_service as people_service
from tests.test_control_people_query import seed

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("stale", [False, True])
def test_people_bulk_flow_uses_the_frozen_hash_and_keeps_selection_and_rows(people_service, locale, stale):
    seed(people_service)
    query = PeopleQuery(tag="GROUP_A")
    before = people_service.people.query(query).model_dump(mode="json")
    review = people_service.bulk.preview(BulkPreviewRequest(action=BulkAction(action="disable"), query=query))
    receipt = people_service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    after = people_service.people.query(query).model_dump(mode="json")
    undone = people_service.bulk.undo(receipt.id)
    result = run_console(
        "people-bulk",
        locale,
        {
            "page": before,
            "after": after,
            "review": review.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json"),
            "undone": undone.model_dump(mode="json"),
            "stale": stale,
        },
    )
    assert result["selected"] == 2
    assert result["previewRequest"]["query"]["tag"] == "GROUP_A"
    assert result["commitRequest"] == {"preview_id": review.id, "preview_hash": review.preview_hash}
    assert result["commits"] == 1
    assert result["sameRows"] and result["keyboard"]
    assert result["stored"] == [] and not result["outside"]
    assert "bulk." not in result["dialog"]
    if stale:
        assert "p-a" in result["dialog"] and result["undos"] == 0
    else:
        assert result["undos"] == 1 and result["undone"]
