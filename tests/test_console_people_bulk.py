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


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_people_refresh_keeps_controls_and_recovers_failed_pagination(people_service, locale):
    from meridian.control.models import UserImport, UserImportBatch

    seed(people_service)
    people_service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username=f"p-refresh-{index:03d}",
                    uuid=f"00000000-0000-4000-8000-{index + 700:012d}",
                    sub_token=f"sample-refresh-token-{index:012d}",
                    tag="GROUP_A",
                )
                for index in range(100)
            ]
        )
    )
    query = PeopleQuery(tag="GROUP_A")
    first = people_service.people.query(query)
    second = people_service.people.query(PeopleQuery(tag="GROUP_A", cursor=first.next_cursor))
    result = run_console(
        "people-bulk-refresh",
        locale,
        {"refresh": True, "page": first.model_dump(mode="json"), "nextPage": second.model_dump(mode="json")},
    )
    assert result["passiveControls"] and result["passiveFocus"]
    assert result["failedRefreshControls"] and result["failedRefreshFocus"]
    assert result["failedNextControls"] and result["failedPreviousControls"]
    assert result["nextPage"] == [person.username for person in second.users]
    assert result["returnedPage"] == [person.username for person in first.users]
    assert result["sameRows"] and result["selected"] == 1
    assert result["stored"] == [] and not result["outside"]
