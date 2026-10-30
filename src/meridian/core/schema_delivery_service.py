"""Schema registration for service-owned delivery operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from meridian.core.notices import Notice, NoticeState, NoticeWrite, RenderedNotice
from meridian.core.schema_envelopes import make_command_envelope


def service_delivery_schemas(
    *, envelope_base: type[BaseModel], failure_data_type: type[BaseModel]
) -> dict[str, type[BaseModel]]:
    schemas: dict[str, type[BaseModel]] = {
        "notice": Notice,
        "notice-state": NoticeState,
        "notice-write": NoticeWrite,
        "rendered-notice": RenderedNotice,
    }
    for action in ("show", "set", "clear"):
        _, _, envelope = make_command_envelope(
            "FleetNotice" + action.capitalize(),
            "fleet.notice." + action,
            NoticeState,
            envelope_base=envelope_base,
            failure_data_type=failure_data_type,
            success_statuses=Literal["ok", "changed", "no_changes"],
            doc=f"Envelope schema for `meridian fleet notice {action} --json`.",
        )
        schemas[f"fleet-notice-{action}-envelope"] = envelope
    return schemas
