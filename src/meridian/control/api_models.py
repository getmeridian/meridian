"""HTTP boundary shapes that compose the service's existing contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, RootModel

from meridian.control.command_models import UserCutOffMutation
from meridian.control.models import UserRecord
from meridian.core.agent_types import MAX_SAFE_INTEGER, Timestamp
from meridian.core.fleet_events import MAX_EVENTS
from meridian.core.models import CoreModel


class UserList(CoreModel):
    users: list[UserRecord]


class DeletedResource(CoreModel):
    deleted: str


class DeployerStateReceipt(CoreModel):
    generation: int = Field(ge=0)
    deposited_at: Timestamp


class UserReissueResponse(RootModel[UserRecord | UserCutOffMutation]):
    """A user, or the new user and queued commands when cut_off is true."""


class UserDeleteCutOffResponse(UserCutOffMutation):
    user: None


class UserDeleteResponse(RootModel[DeletedResource | UserDeleteCutOffResponse]):
    """A deletion receipt, or queued cut-off commands with user set to null."""


class SnapshotResponse(RootModel[bytes]):
    """An encrypted snapshot, returned as application/octet-stream."""


class HistoryQuery(CoreModel):
    days: int = Field(default=30, ge=1, le=730, description="UTC days including today, capped at retained history.")


class FleetEventsQuery(CoreModel):
    after: int = Field(default=0, ge=0, le=MAX_SAFE_INTEGER, description="Return events after this cursor.")
    limit: int = Field(default=100, ge=1, le=MAX_EVENTS)


class OpenApiInfo(CoreModel):
    title: str
    version: str
    description: str


class OpenApiServer(CoreModel):
    url: str
    description: str


class OpenApiDocument(CoreModel):
    """OpenAPI 3.1 with Pydantic-generated schemas for the scoped admin API."""

    openapi: Literal["3.1.0"] = "3.1.0"
    json_schema_dialect: str = Field(validation_alias="jsonSchemaDialect", serialization_alias="jsonSchemaDialect")
    info: OpenApiInfo
    servers: list[OpenApiServer]
    paths: dict[str, dict[str, Any]]
    components: dict[str, Any]
    security: list[dict[str, list[str]]]
    scope_grants: dict[str, list[str]] = Field(
        validation_alias="x-meridian-scope-grants", serialization_alias="x-meridian-scope-grants"
    )
