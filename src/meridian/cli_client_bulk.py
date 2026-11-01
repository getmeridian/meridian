"""Bulk client command registration; all actions share the people-query flags."""

from __future__ import annotations

from typing import Annotated

import typer

from meridian.cli_options import (
    DeviceLimit,
    Expires,
    Json,
    NoRenewal,
    RenewalCalendarMonths,
    RenewalCap,
    RenewalDays,
    ResetAnchorDay,
    TrafficLimit,
    TrafficResetPeriod,
    Yes,
)
from meridian.cli_people import (
    Attention,
    Expiring,
    Lifecycle,
    Never,
    Order,
    Persons,
    Quota,
    Reason,
    Search,
    Sort,
    Status,
    Tag,
)

bulk_app = typer.Typer(help="Review and change many people in one transaction", no_args_is_help=True)
DryRun = Annotated[
    bool, typer.Option("--dry-run", help="Return the frozen preview without committing", rich_help_panel="Review")
]


@bulk_app.command("extend", rich_help_panel="Reviewed changes")
def bulk_extend(
    days: str | None = typer.Option(None, "--days", help="Extend by 1–3650 whole days"),
    months: str | None = typer.Option(None, "--months", help="Extend by 1–120 calendar months"),
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review extend for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("extend", locals())


@bulk_app.command("expiry", rich_help_panel="Reviewed changes")
def bulk_expiry(
    expires: Expires = None,
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review expiry for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("expiry", locals())


@bulk_app.command("quota", rich_help_panel="Reviewed changes")
def bulk_quota(
    limit: TrafficLimit = None,
    reset: TrafficResetPeriod = None,
    reset_day: ResetAnchorDay = None,
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review quota for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("quota", locals())


@bulk_app.command("reset-traffic", rich_help_panel="Reviewed changes")
def bulk_reset_traffic(
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Reset selected people's period traffic after reviewing the exact changes."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("reset-traffic", locals())


@bulk_app.command("enable", rich_help_panel="Reviewed changes")
def bulk_enable(
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review enable for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("enable", locals())


@bulk_app.command("disable", rich_help_panel="Reviewed changes")
def bulk_disable(
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review disable for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("disable", locals())


@bulk_app.command("tag", rich_help_panel="Reviewed changes")
def bulk_tag(
    set_tag: str | None = typer.Option(None, "--set-tag", help="Set a tag; none clears it"),
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review tag for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("tag", locals())


@bulk_app.command("devices", rich_help_panel="Reviewed changes")
def bulk_devices(
    devices: DeviceLimit = None,
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review devices for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("devices", locals())


@bulk_app.command("renewal", rich_help_panel="Reviewed changes")
def bulk_renewal(
    renew_days: RenewalDays = None,
    renew_months: RenewalCalendarMonths = None,
    renewals: RenewalCap = None,
    no_renew: NoRenewal = False,
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review renewal for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("renewal", locals())


@bulk_app.command("delete", rich_help_panel="Reviewed changes")
def bulk_delete(
    q: Search = "",
    status: Status = "",
    tag: Tag = None,
    expiring: Expiring = None,
    quota: Quota = None,
    never: Never = False,
    attention: Attention = False,
    reason: Reason = "",
    lifecycle: Lifecycle = "",
    sort: Sort = "name",
    order: Order = None,
    persons: Persons = None,
    dry_run: DryRun = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review delete for selected people, then commit the exact preview."""
    from meridian.commands.client_bulk import run_bulk

    run_bulk("delete", locals())


@bulk_app.command("undo", rich_help_panel="Receipts")
def bulk_undo(
    receipt_id: str = typer.Argument(..., help="ID returned by a bulk commit"),
    yes: Yes = False,
    dry_run: DryRun = False,
    _json: Json = False,
) -> None:
    """Undo reversible fields within ten minutes when every person is unchanged."""
    from meridian.commands.client_bulk import run_receipt

    run_receipt(receipt_id, undo=True, yes=yes, dry_run=dry_run)


@bulk_app.command("receipt", rich_help_panel="Receipts")
def bulk_receipt(
    receipt_id: str = typer.Argument(..., help="ID returned by a bulk commit"),
    _json: Json = False,
) -> None:
    """Read a durable receipt without private notes or credentials."""
    from meridian.commands.client_bulk import run_receipt

    run_receipt(receipt_id)
