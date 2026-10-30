"""Server onboarding entries for the public schema catalog."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from meridian.control.models import ServiceSettings
from meridian.core.schema_envelopes import make_command_envelope
from meridian.core.server_allowances import (
    ServerAllowanceSpec,
    ServerListResult,
    ServerMetadata,
    ServerMetadataChange,
    ServerSetRequest,
    ServerSetResult,
    ServerUsageList,
)
from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerBootstrapKeyResult,
    ServerConnectionDraft,
    ServerProfile,
    ServerValidateRequest,
    ServerValidateResult,
)

SERVER_SCHEMAS: dict[str, type[BaseModel]] = {
    "server-metadata": ServerMetadata,
    "server-metadata-change": ServerMetadataChange,
    "server-allowance-spec": ServerAllowanceSpec,
    "server-list": ServerListResult,
    "server-set": ServerSetResult,
    "server-set-request": ServerSetRequest,
    "server-usage": ServerUsageList,
    "service-settings": ServiceSettings,
    "server-bootstrap-key-request": ServerBootstrapKeyRequest,
    "server-bootstrap-key-result": ServerBootstrapKeyResult,
    "server-connection-draft": ServerConnectionDraft,
    "server-profile": ServerProfile,
    "server-validate-request": ServerValidateRequest,
    "server-validate-result": ServerValidateResult,
}


def server_command_schemas(base: type[BaseModel], empty: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """Compose server command envelopes with the catalog's shared base and terminal body."""
    _success, _terminal, list_envelope = make_command_envelope(
        "ServerList",
        "server.list",
        ServerListResult,
        envelope_base=base,
        failure_data_type=empty,
        doc="Envelope schema for `meridian server list --json`.",
    )
    _success, _terminal, set_envelope = make_command_envelope(
        "ServerSet",
        "server.set",
        ServerSetResult,
        envelope_base=base,
        failure_data_type=empty,
        success_statuses=Literal["ok", "changed", "no_changes"],
        doc="Envelope schema for `meridian server set NAME --json`.",
    )
    return {"server-list-envelope": list_envelope, "server-set-envelope": set_envelope}
