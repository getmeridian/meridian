"""Shared people-selection flags; adapters build the same service query."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from meridian.core.people_query import PeopleQuery
from meridian.core.person_metadata import parse_tag_option
from meridian.core.queries import bounded_integer

Search = Annotated[
    str, typer.Option("--search", help="Search username or display name", rich_help_panel="Matching people")
]
Status = Annotated[
    str, typer.Option("--status", help="active, disabled, expired, or limited", rich_help_panel="Matching people")
]
Tag = Annotated[
    str | None, typer.Option("--tag", help="Match a tag; none for untagged", rich_help_panel="Matching people")
]
Expiring = Annotated[
    str | None,
    typer.Option(
        "--expiring-within", metavar="DAYS", help="Expiry within 1–365 days", rich_help_panel="Matching people"
    ),
]
Quota = Annotated[
    str | None,
    typer.Option(
        "--quota-at-least", metavar="PERCENT", help="Quota share at or above 1–100%", rich_help_panel="Matching people"
    ),
]
Never = Annotated[
    bool, typer.Option("--never-used", help="No known lifetime use or activity", rich_help_panel="Matching people")
]
Attention = Annotated[
    bool, typer.Option("--attention", help="Any default attention reason", rich_help_panel="Matching people")
]
Reason = Annotated[
    str,
    typer.Option("--attention-reason", help="expiring, quota, unused, or stopped", rich_help_panel="Matching people"),
]
Lifecycle = Annotated[
    str, typer.Option("--lifecycle", help="pending, renewing, fixed, or unlimited", rich_help_panel="Matching people")
]
Sort = Annotated[
    str, typer.Option("--sort", help="name, usage, expiry, or activity", rich_help_panel="Matching people")
]
Order = Annotated[
    str | None, typer.Option("--order", help="asc or desc; default follows the sort", rich_help_panel="Matching people")
]
Persons = Annotated[
    list[str] | None,
    typer.Option(
        "--person",
        metavar="USERNAME",
        help="Select exact usernames instead of a query",
        rich_help_panel="Matching people",
    ),
]


def people_query(options: dict[str, Any]) -> PeopleQuery:
    if options.get("attention") and options.get("reason"):
        raise ValueError("Choose --attention or --attention-reason.")
    tag = options.get("tag")
    wanted_tag = parse_tag_option(tag) if tag is not None else None
    return PeopleQuery(
        q=options.get("q", ""),
        status=options.get("status", ""),
        tag=("none" if wanted_tag == "" else wanted_tag) if wanted_tag is not None else "",
        expires=bounded_integer(options["expiring"], "--expiring-within", 1, 365)
        if options.get("expiring") is not None
        else None,
        quota=bounded_integer(options["quota"], "--quota-at-least", 1, 100)
        if options.get("quota") is not None
        else None,
        never=options.get("never", False),
        attention="all" if options.get("attention") else options.get("reason", ""),
        lifecycle=options.get("lifecycle", ""),
        sort=options.get("sort", "name"),
        order=options.get("order"),
        limit=bounded_integer(options["page_size"], "--limit", 1, 500) if options.get("page_size") is not None else 100,
        cursor=options.get("cursor"),
    )
