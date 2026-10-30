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
            description="Scoped bearer-token operations behind the operator's private console address. "
            "The read, propose, and approve scopes form a chain; people is separate, and admin covers both. "
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
                    "description": "Send Authorization: Bearer <token>. Required scopes are declared by "
                    "x-meridian-scopes; either listed scope or a scope that covers it is sufficient.",
                }
            },
        },
        security=[{"meridianToken": []}],
        scope_grants={held: [needed for needed in TOKEN_SCOPES if scope_covers(held, needed)] for held in TOKEN_SCOPES},
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
    description = f"Requires {' or '.join(item.scopes)} scope, or a scope that covers it."
    if item.description:
        description += " " + item.description
    for condition in item.body_scopes:
        scopes = (condition.scope,) if isinstance(condition.scope, str) else condition.scope
        description += (
            f" Setting {condition.field} to {str(condition.equals).lower()} also requires {' or '.join(scopes)} scope."
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
        "x-meridian-scope-mode": "any",
        "x-meridian-credentials": item.credentials,
        "responses": {
            "200": {
                "description": "Successful response." + (" " + CREDENTIAL_SENTENCE if item.credentials else ""),
                "x-meridian-credentials": item.credentials,
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
