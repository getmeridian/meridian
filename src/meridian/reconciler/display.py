"""Rich terminal display for reconciliation plans.

Terraform-style output: ``+`` for adds, ``-`` for removes, ``~`` for
updates. Color-coded: green for add, red for remove, yellow for update.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from meridian.reconciler.diff import Plan, PlanAction


def print_plan(plan: Plan, console: Console | None = None) -> None:
    """Print a reconciliation plan to the terminal."""
    if console is None:
        import sys

        console = Console(stderr=True, file=sys.stderr)

    if plan.is_empty:
        console.print("\n[green]No changes.[/green] Infrastructure is up to date.\n")
        return

    console.print()
    for action in plan.actions:
        _print_action(action, console)

    console.print()
    console.print(f"[bold]{plan.summary()}[/bold]")
    if plan.has_destructive:
        console.print("[yellow]Warning: plan includes destructive actions (removals).[/yellow]")
    console.print()


def _print_action(action: PlanAction, console: Console) -> None:
    """Print a single plan action."""
    symbol = action.symbol
    if symbol == "+":
        color = "green"
    elif symbol == "-":
        color = "red"
    else:
        color = "yellow"

    kind_label = action.kind.value.replace("_", " ")
    console.print(f"  [{color}]{symbol}[/{color}] {kind_label}: [bold]{escape(action.target)}[/bold]")
    if action.detail:
        console.print(f"      {escape(action.detail)}")
