"""Bulk envelope registration without growing the central schema catalog."""

from typing import Any

from pydantic import BaseModel

from meridian.core.people_bulk import ClientBulkResult
from meridian.core.schema_envelopes import make_command_envelope


def bulk_envelopes(*, envelope_base: type[BaseModel], failure_data_type: Any) -> dict[str, type[BaseModel]]:
    result: dict[str, type[BaseModel]] = {}
    for action in (
        "extend",
        "expiry",
        "quota",
        "reset-traffic",
        "enable",
        "disable",
        "tag",
        "devices",
        "renewal",
        "delete",
        "undo",
        "receipt",
    ):
        prefix = "ClientBulk" + action.title().replace("-", "")
        _, _, model = make_command_envelope(
            prefix,
            "client.bulk." + action,
            ClientBulkResult,
            envelope_base=envelope_base,
            failure_data_type=failure_data_type,
        )
        result["client-bulk-" + action + "-envelope"] = model
    return result
