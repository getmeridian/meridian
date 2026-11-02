"""Human policy tables over the same typed preview returned by the service."""

from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table

from meridian import console
from meridian.core.delivery_preview import DeliveryPolicyPreview
from meridian.i18n import t


def print_delivery_preview(preview: DeliveryPolicyPreview) -> None:
    console.info(t("policy.current", policy=preview.effective.policy.id))
    for format in preview.formats:
        console.info(t(f"preview.format_{format.format}"))
        table = Table(box=box.SIMPLE, padding=(0, 1))
        table.add_column(t("policy.rule"))
        table.add_column(t("policy.match"))
        table.add_column(t("policy.action"))
        for rule in format.rules:
            match = [*rule.domains, *rule.ips, *rule.networks, *(port.text() for port in rule.ports), *rule.protocols]
            action = (
                t("policy.action_exit", exit=rule.action.exit)
                if rule.action.kind == "exit"
                else t(f"policy.action_{rule.action.kind}")
            )
            table.add_row(str(rule.number), escape(" · ".join(match)), escape(action))
        console.err_console.print(table)
        for omission in format.omitted:
            text = t(f"policy.omit_{omission.reason}")
            if omission.rule is not None:
                text = t("policy.rule_number", rule=omission.rule) + ": " + text
            if omission.context is not None:
                text += " · " + t(f"policy.{omission.context}", count=omission.profile_count)
            console.info(text)
    console.info(t("policy.geodata"))
