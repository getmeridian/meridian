"""Typed, people-capable queries, frozen previews, commits, receipts, and undo."""

from meridian.control import people_routes
from meridian.control.route_metadata import NOT_FOUND
from meridian.control.route_types import ApiRoute, ErrorResponse, route
from meridian.core.people_bulk import BulkCommitRequest, BulkPreview, BulkPreviewRequest, BulkReceipt, BulkUndoRequest
from meridian.core.people_query import PeoplePage, PeopleQuery

PEOPLE_ROUTES: tuple[ApiRoute, ...] = (
    route(
        "GET",
        "/api/people",
        "people",
        people_routes.query_people,
        None,
        PeoplePage,
        "Find people with shared filters, stable cursor pages, and contextual counts.",
        credentials=False,
        capability=("people.query", "people.private"),
        query_model=PeopleQuery,
        description=(
            "Case-folded username/display-name search; missing dates sort last. "
            "Cursors exclude later insertions and expire after 15 minutes."
        ),
    ),
    route(
        "POST",
        "/api/people/bulk/preview",
        "people",
        people_routes.preview_people_bulk,
        BulkPreviewRequest,
        BulkPreview,
        "Freeze a bulk selection and review every before/after without writing.",
        credentials=False,
        capability=("people.batch", "people.private"),
        refusals=(NOT_FOUND, ErrorResponse(409, ("conflicting_delivery_policies",))),
        description=(
            "Previews write no database rows or events. They expire after 10 minutes or a service restart. "
            "Declared deletions return exact reviewed CLI commands."
            " Profile choices resolve each person's delivery policy before returning a review."
        ),
    ),
    route(
        "POST",
        "/api/people/bulk/commit",
        "people",
        people_routes.commit_people_bulk,
        BulkCommitRequest,
        BulkReceipt,
        "Commit the frozen review atomically and return a durable receipt.",
        credentials=False,
        capability=("people.batch", "people.private"),
        refusals=(
            ErrorResponse(
                409, ("stale_people", "preview_expired", "preview_mismatch", "conflicting_delivery_policies")
            ),
        ),
        description=(
            "Requires the preview ID and hash. "
            "Any changed person or enforcement result refuses the entire operation. A retry returns the same receipt."
        ),
    ),
    route(
        "GET",
        "/api/people/bulk/receipts/{receipt_id}",
        "people",
        people_routes.people_bulk_receipt,
        None,
        BulkReceipt,
        "Read a completed bulk receipt without notes or credentials.",
        credentials=False,
        capability=("people.batch", "people.private"),
        refusals=(NOT_FOUND,),
    ),
    route(
        "POST",
        "/api/people/bulk/undo",
        "people",
        people_routes.undo_people_bulk,
        BulkUndoRequest,
        BulkReceipt,
        "Undo reversible field changes while every person is unchanged.",
        credentials=False,
        capability=("people.batch", "people.private"),
        refusals=(NOT_FOUND, ErrorResponse(409, ("stale_people", "undo_refused", "conflicting_delivery_policies"))),
        description=(
            "Undo lasts 10 minutes, survives service restarts, and is single-use. Reset and delete cannot be undone. "
            "dry_run=true checks people, grants, nodes, restored profiles, and delivery defaults without writing, "
            "returning undo_people."
        ),
    ),
)
