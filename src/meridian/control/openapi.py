"""Generate the admin OpenAPI 3.1 document from routing and Pydantic schemas."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, models_json_schema

from meridian import __version__
from meridian.control.api_models import OpenApiDocument, OpenApiInfo, OpenApiServer
from meridian.control.models import TOKEN_SCOPES, scope_covers
from meridian.control.route_catalog import API_ROUTES
from meridian.control.route_types import ApiRoute, path_schema
from meridian.control.token_capabilities import legacy_capabilities
from meridian.core.version_skew import SERVICE_VERSION_HEADER

CREDENTIAL_SENTENCE = "This response can contain credentials; keep it private and do not log it."


@lru_cache(maxsize=8)
def openapi_document(version: str = __version__) -> OpenApiDocument:
    """The installed service supplies its own version; exports use the package version."""
    routes = [item for item in API_ROUTES if item.internal_reason is None]
    models: set[tuple[type[BaseModel], JsonSchemaMode]] = set()
    for item in routes:
        models.add((item.response_model, "serialization"))
        for error in item.errors:
            models.add((error.model, "serialization"))
        for model in (item.request_model, item.query_model):
            if model is not None:
                models.add((model, "validation"))
    model_schemas, definitions = models_json_schema(
        sorted(models, key=lambda pair: (pair[0].__module__, pair[0].__name__, pair[1])),
        ref_template="#/components/schemas/{model}",
    )
    paths: dict[str, dict[str, Any]] = {}
    operation_ids: set[str] = set()
    for item in routes:
        if item.operation_id in operation_ids or item.method.lower() in paths.get(item.path, {}):
            raise ValueError("Admin API operations must have unique IDs and method/path pairs.")
        operation_ids.add(item.operation_id)
        paths.setdefault(item.path, {})[item.method.lower()] = _operation(item, model_schemas, definitions["$defs"])

    return OpenApiDocument(
        json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
        info=OpenApiInfo(
            title="Meridian admin API",
            version=version,
            description="Explicit-capability bearer-token operations behind the operator's private console address. "
            "Capabilities are declared per action and frozen on tokens. Compatible read/propose/approve/people/admin "
            "presets expand to equivalent grants; credentials and private metadata require explicit permissions. "
            "Node enrollment, agent, subscription, page, and console-session surfaces are internal or public "
            "and are excluded from this document.",
        ),
        servers=[
            OpenApiServer(
                url="..",
                description="The console base address, including nginx's secret path. "
                "Resolved relative to /api/openapi.json; set this base explicitly when using a downloaded document.",
            )
        ],
        paths=paths,
        components={
            "schemas": definitions["$defs"],
            "securitySchemes": {
                "meridianToken": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Meridian scoped API token",
                    "description": "Send Authorization: Bearer <token>. All x-meridian-capabilities are required; "
                    "conditional capabilities describe body choices and response fields. x-meridian-scopes records "
                    "compatible presets, expanded by x-meridian-capability-grants.",
                }
            },
        },
        security=[{"meridianToken": []}],
        scope_grants={held: [needed for needed in TOKEN_SCOPES if scope_covers(held, needed)] for held in TOKEN_SCOPES},
        capability_grants={scope: sorted(legacy_capabilities(scope)) for scope in TOKEN_SCOPES},
    )


# RFC 9110 phrases where Python's `HTTPStatus` changed them in 3.13, so every supported Python writes one document.
_RFC9110_PHRASES = {
    413: "Content Too Large",
    414: "URI Too Long",
    416: "Range Not Satisfiable",
    422: "Unprocessable Content",
}


def _status_phrase(status: int) -> str:
    return _RFC9110_PHRASES.get(status) or HTTPStatus(status).phrase


def _operation(
    item: ApiRoute,
    schemas: Mapping[tuple[Any, JsonSchemaMode], dict[str, Any]],
    definitions: dict[str, Any],
) -> dict[str, Any]:
    description = (
        f"Requires {' and '.join(item.capabilities)} capabilities. "
        f"Compatible presets: {' or '.join(item.scopes)}, including scopes that cover them."
    )
    if item.description:
        description += " " + item.description
    for condition in item.body_scopes:
        description += f" Setting {condition.field} to {str(condition.equals).lower()} " + (
            f"requires {condition.capability} instead."
            if condition.replaces
            else f"also requires {condition.capability}."
        )
    if item.credentials:
        description += " " + CREDENTIAL_SENTENCE
    operation: dict[str, Any] = {
        "operationId": item.operation_id,
        "summary": item.summary,
        "description": description,
        "tags": ["contract" if item.path == "/api/openapi.json" else item.path.split("/")[2]],
        "security": [{"meridianToken": []}],
        "x-meridian-scopes": list(item.scopes),
        "x-meridian-capabilities": list(item.capabilities),
        "x-meridian-capability-mode": "all",
        "x-meridian-scope-mode": "any",
        "x-meridian-credentials": item.credentials,
        "responses": {
            "200": {
                "description": "Successful response." + (" " + CREDENTIAL_SENTENCE if item.credentials else ""),
                "x-meridian-credentials": item.credentials,
                "headers": {
                    SERVICE_VERSION_HEADER: {
                        "description": "Installed service version; sent only after API authentication.",
                        "schema": {"type": "string"},
                    }
                },
                "content": {item.content_type: {"schema": schemas[(item.response_model, "serialization")]}},
            }
        },
    }
    if item.body_scopes:
        operation["x-meridian-conditional-scopes"] = [
            {
                "field": condition.field,
                "equals": condition.equals,
                "scopes": [condition.scope] if isinstance(condition.scope, str) else list(condition.scope),
            }
            for condition in item.body_scopes
        ]
        operation["x-meridian-conditional-capabilities"] = [
            {
                "field": rule.field,
                "equals": rule.equals,
                "capability": rule.capability,
                "mode": "replace" if rule.replaces else "additional",
            }
            for rule in item.body_scopes
        ]
    if item.extra_capabilities:
        operation["x-meridian-extra-capabilities"] = {rule.purpose: rule.capability for rule in item.extra_capabilities}
    parameters = [
        {"name": name, "in": "path", "required": True, "schema": path_schema(name)}
        for name in re.findall(r"\{([a-z_]+)\}", item.path)
    ]
    if item.query_model is not None:
        query = definitions[schemas[(item.query_model, "validation")]["$ref"].rsplit("/", 1)[1]]
        parameters.extend(
            {"name": name, "in": "query", "required": name in query.get("required", []), "schema": schema}
            for name, schema in query["properties"].items()
        )
    if parameters:
        operation["parameters"] = parameters
    if item.request_model is not None:
        operation["requestBody"] = {
            "required": item.body_required,
            "content": {"application/json": {"schema": schemas[(item.request_model, "validation")]}},
        }
    for error in item.errors:
        status = str(error.status)
        response = operation["responses"].setdefault(
            status,
            {
                "description": _status_phrase(error.status),
                "content": {"application/json": {"schema": schemas[(error.model, "serialization")]}},
                "x-meridian-error-codes": [],
            },
        )
        response["x-meridian-error-codes"] = sorted(set(response["x-meridian-error-codes"]) | set(error.codes))
    return operation
