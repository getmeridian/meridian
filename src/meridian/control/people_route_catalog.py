"""Typed, people-capable queries, frozen previews, commits, receipts, and undo."""

from meridian.control import people_routes
from meridian.control.route_types import ApiRoute, route
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
        query_model=PeopleQuery,
        description=(
            "Case-folded username/display-name search; missing dates sort last. "
            "Cursors exclude later insertions and expire after 15 minutes."
        ),
    ),
)
