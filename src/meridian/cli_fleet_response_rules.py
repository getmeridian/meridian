"""Transient app matcher commands beside reviewed fleet routing."""

from __future__ import annotations

import typer

from meridian.cli_options import Json

response_rules_app = typer.Typer(
    help="Test reviewed app response rules without fetching a subscription", no_args_is_help=True
)


@response_rules_app.command("test")
def response_rules_test(
    user_agent: str = typer.Option("", "--user-agent", help="Synthetic User-Agent, up to 512 characters"),
    accept: str = typer.Option("", "--accept", help="Synthetic Accept header, up to 256 characters"),
    person: str | None = typer.Option(None, "--person", help="Include a person's private policy override and profiles"),
    suffix: str = typer.Option(
        "", "--suffix", help="Explicit info, json, v2ray-json, mihomo, clash, stash, or singbox suffix"
    ),
    _json: Json = False,
) -> None:
    """Explain the first app rule and its format, refusal, policy, and header changes"""
    from meridian.commands.response_rules import run_test

    run_test(user_agent=user_agent, accept=accept, person=person, suffix=suffix)
