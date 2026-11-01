"""CLI adapter for the same people query used by the console and bulk selection."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from meridian.cli_people import people_query
from meridian.console import error_context, fail
from meridian.control.http_errors import validation_message
from meridian.core.errors import MeridianError


def run_list_query(options: dict[str, Any]) -> None:
    with error_context("client.list"):
        return run_validated_list(options)


def run_validated_list(options: dict[str, Any]) -> None:
    from meridian.commands.client import run_list

    try:
        query = people_query(options)
    except (ValidationError, ValueError, MeridianError) as exc:
        fail(validation_message(exc) if isinstance(exc, ValidationError) else str(exc), hint_type="user")
    run_list(query=query)
