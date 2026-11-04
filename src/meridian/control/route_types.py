"""Shared route metadata and named path matching for the admin catalog."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from meridian.control.http_errors import ApiErrorResponse
from meridian.control.models import DeviceKeyRecord, ScopeRequirement, TokenRecord, TokenScope, UserPatch
from meridian.core.agent_types import NodeId
from meridian.core.client_devices import ClientDevice
from meridian.core.token_models import Permission, PermissionRequirement, TokenCapability

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
Handler = str | Callable[[Any, re.Match[str]], Any]
WRITE: TokenScope = "admin"

# Preserve the accepted path alphabets, including the command lookup's existing
# 36-character hex/hyphen form. Tightening any of these is a separate API change.
PATH_TYPES: dict[str, Any] = {
    "node_id": NodeId,
    "username": UserPatch.model_fields["username"].rebuild_annotation(),
    "endpoint_id": Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")],
    "token_id": TokenRecord.model_fields["id"].rebuild_annotation(),
    "device_id": DeviceKeyRecord.model_fields["id"].rebuild_annotation(),
    "receipt_id": Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
    "revision": Annotated[str, Field(pattern=r"^[1-9][0-9]{0,15}$")],
    "command_id": Annotated[str, Field(pattern=r"^[0-9a-f-]{36}$")],
    "device_key": ClientDevice.model_fields["device_key"].rebuild_annotation(),
}


def path_schema(name: str) -> dict[str, Any]:
    return TypeAdapter(PATH_TYPES[name]).json_schema()


def path_pattern(template: str) -> re.Pattern[str]:
    """Compile named parameters with the same bounds used in the document."""
    parts: list[str] = []
    offset = 0
    for match in re.finditer(r"\{([a-z_]+)\}", template):
        name = match[1]
        schema = path_schema(name)
        pattern = schema["pattern"].removeprefix("^").removesuffix("$")
        # A length bound kept outside the pattern (topology IDs) still bounds the segment.
        if "maxLength" in schema:
            pattern = f"(?=[^/]{{1,{schema['maxLength']}}}(?![^/])){pattern}"
        parts.extend((re.escape(template[offset : match.start()]), f"(?P<{name}>{pattern})"))
        offset = match.end()
    parts.append(re.escape(template[offset:]))
    return re.compile("".join(parts))


@dataclass(frozen=True)
class ErrorResponse:
    status: int
    codes: tuple[str, ...]
    model: type[BaseModel] = ApiErrorResponse


@dataclass(frozen=True)
class BodyScope:
    field: str
    equals: bool | str
    scope: ScopeRequirement
    capability: TokenCapability
    replaces: bool = False


@dataclass(frozen=True)
class ExtraCapability:
    purpose: str
    capability: TokenCapability
    scope: ScopeRequirement


@dataclass(frozen=True)
class ApiRoute:
    method: HttpMethod
    path: str
    scope: ScopeRequirement
    handler: Handler
    request_model: type[BaseModel] | None
    response_model: type[BaseModel]
    errors: tuple[ErrorResponse, ...]
    summary: str
    credentials: bool
    capability: PermissionRequirement
    query_model: type[BaseModel] | None = None
    body_required: bool = True
    body_scopes: tuple[BodyScope, ...] = ()
    content_type: str = "application/json"
    description: str = ""
    internal_reason: str | None = None
    extra_capabilities: tuple[ExtraCapability, ...] = ()

    @property
    def operation_id(self) -> str:
        return self.handler if isinstance(self.handler, str) else self.handler.__name__

    @property
    def scopes(self) -> tuple[TokenScope, ...]:
        return (self.scope,) if isinstance(self.scope, str) else self.scope

    @property
    def capabilities(self) -> tuple[Permission, ...]:
        return (self.capability,) if isinstance(self.capability, str) else self.capability

    @property
    def conditional_capabilities(self) -> tuple[BodyScope | ExtraCapability, ...]:
        return (*self.body_scopes, *self.extra_capabilities)


def route(
    method: HttpMethod,
    path: str,
    scope: ScopeRequirement,
    handler: Handler,
    request: type[BaseModel] | None,
    response: type[BaseModel],
    summary: str,
    *,
    credentials: bool,
    capability: PermissionRequirement,
    refusals: tuple[ErrorResponse, ...] = (),
    query_model: type[BaseModel] | None = None,
    **options: Any,
) -> ApiRoute:
    errors = [
        ErrorResponse(401, ("unauthenticated",)),
        ErrorResponse(403, ("insufficient_scope",)),
        ErrorResponse(500, ("internal",)),
    ]
    if request is not None or query_model is not None:
        errors.append(ErrorResponse(400, ("invalid_request",)))
    if request is not None:
        errors.append(ErrorResponse(413, ("payload_too_large",)))
    return ApiRoute(
        method,
        path,
        scope,
        handler,
        request,
        response,
        (*errors, *refusals),
        summary,
        credentials,
        capability,
        query_model=query_model,
        **options,
    )
