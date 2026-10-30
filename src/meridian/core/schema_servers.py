"""Server onboarding entries for the public schema catalog."""

from __future__ import annotations

from pydantic import BaseModel

from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerBootstrapKeyResult,
    ServerConnectionDraft,
    ServerProfile,
    ServerValidateRequest,
    ServerValidateResult,
)

SERVER_SCHEMAS: dict[str, type[BaseModel]] = {
    "server-bootstrap-key-request": ServerBootstrapKeyRequest,
    "server-bootstrap-key-result": ServerBootstrapKeyResult,
    "server-connection-draft": ServerConnectionDraft,
    "server-profile": ServerProfile,
    "server-validate-request": ServerValidateRequest,
    "server-validate-result": ServerValidateResult,
}
