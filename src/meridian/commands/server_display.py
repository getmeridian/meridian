"""Localized server metadata and allowance evidence for list, review, and fleet status."""

from __future__ import annotations

from rich.markup import escape

from meridian import console
from meridian.commands._helpers import format_traffic
from meridian.core.attention import ServerAttention
from meridian.core.server_allowances import ServerMetadata, ServerUsage
from meridian.i18n import t


def print_metadata(metadata: ServerMetadata) -> None:
    if console.is_json_mode():
        return
    if metadata.provider:
        console.info(t("server_metadata.provider", provider=metadata.provider))
    console.info(
        t(
            "server_metadata.allowance",
            allowance=format_traffic(metadata.allowance_bytes)
            if metadata.allowance_bytes is not None
            else t("server_metadata.no_allowance"),
        )
    )
    if metadata.allowance_bytes is not None:
        console.info(t("server_metadata.reset_day", day=metadata.reset_day))
        console.info(t("server_metadata.approximate"))
    if metadata.pay_on is not None:
        console.info(
            t(
                "server_metadata.payment_anchor",
                date=str(metadata.pay_on),
                repeat=t(f"server_metadata.repeat.{metadata.pay_every or 'none'}"),
            )
        )


def print_server_usage(servers: list[ServerUsage], attention: list[ServerAttention]) -> None:
    if console.is_json_mode() or not servers:
        return
    console.err_console.print()
    console.err_console.print("  [bold]" + escape(t("server_metadata.title")) + "[/bold]")
    reasons = {item.server_ref: item.reasons for item in attention}
    for server in servers:
        console.err_console.print("    [bold]" + escape(server.server_ref) + "[/bold]")
        print_metadata(server.metadata)
        if server.measurement == "not_measured":
            console.info(t("server_metadata.not_measured"))
        elif server.used_bytes is not None:
            console.info(
                t(
                    "server_metadata.used",
                    traffic=format_traffic(server.used_bytes, server.metadata.allowance_bytes or 0),
                )
            )
            console.info(t("server_metadata.period", start=server.period_start or "", end=server.period_end or ""))
            if server.counting_since != server.period_start:
                console.info(t("server_metadata.since", date=server.counting_since or ""))
        if server.next_payment_on is not None:
            console.info(t("server_metadata.next_payment", date=str(server.next_payment_on)))
        for reason in reasons.get(server.server_ref, []):
            console.warn(t(f"server_metadata.attention.{reason}"))
        console.err_console.print("      " + escape(t("server_metadata.change_cli")))
