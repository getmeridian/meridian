"""Construct command envelopes while preserving the catalog's model names and wire fields."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeVar, Union

from pydantic import BaseModel, Field, RootModel, create_model

from meridian.core.models import ErrorModel

EnvelopeModel = TypeVar("EnvelopeModel", bound=BaseModel)


def make_command_envelope(
    prefix: str,
    command: str,
    data_type: Any,
    *,
    envelope_base: type[EnvelopeModel],
    success_statuses: Any = None,
    failure_data_type: Any,
    doc: str = "",
) -> tuple[type[EnvelopeModel], type[EnvelopeModel], type[RootModel[Any]]]:
    """Build (SuccessEnvelope, TerminalEnvelope, OutputEnvelope) for a command.

    Model names, discriminator mappings, and fields match the catalog's existing
    envelopes; the catalog supplies its shared base and empty failure-data model.
    """
    cmd_literal = Literal[command]  # type: ignore[valid-type]
    if success_statuses is None:
        success_statuses = Literal["ok"]
    failed_status: Any = Literal["failed"]

    success_cls = create_model(
        f"_{prefix}SuccessEnvelope",
        __base__=envelope_base,
        command=(cmd_literal, ...),
        status=(success_statuses, ...),
        data=(data_type, ...),
        errors=(list[ErrorModel], Field(max_length=0)),
    )

    terminal_cls = create_model(
        f"_{prefix}TerminalEnvelope",
        __base__=envelope_base,
        command=(cmd_literal, ...),
        status=(failed_status, ...),
        data=(failure_data_type, ...),
        errors=(list[ErrorModel], Field(min_length=1)),
    )

    union_type = Annotated[Union[success_cls, terminal_cls], Field(discriminator="status")]  # type: ignore[valid-type]
    root_cls = type(f"{prefix}OutputEnvelope", (RootModel[union_type],), {"__doc__": doc})
    return success_cls, terminal_cls, root_cls
