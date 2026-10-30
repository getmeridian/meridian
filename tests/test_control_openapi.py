"""The admin HTTP contract is served, exported, and checked for drift."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from meridian import __version__
from meridian.control.http_errors import ApiErrorResponse
from meridian.control.models import TOKEN_SCOPES, UserRecord, scope_covers
from meridian.control.openapi import CREDENTIAL_SENTENCE, openapi_document
from meridian.control.route_catalog import API_ROUTES
from meridian.control.route_table import INTERNAL_ROUTES, INTERNAL_UI_ROUTES
from meridian.control.route_types import path_pattern
from meridian.control.server import ALL_ROUTES
from meridian.core.client_limits import MAX_TRAFFIC_LIMIT_BYTES
from scripts.export_contracts import check_contracts, write_contracts
from tests.support.control_service import ADMIN_TOKEN, serve_control


@pytest.mark.parametrize("scope", TOKEN_SCOPES)
def test_admin_openapi_is_served_to_any_valid_token(tmp_path: Path, scope: str) -> None:
    with serve_control(tmp_path) as url, httpx.Client(base_url=url) as client:
        issued = client.post(
            "/api/tokens",
            json={"name": "integration", "scope": scope},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert issued.status_code == 200
        response = client.get("/api/openapi.json", headers={"Authorization": f"Bearer {issued.json()['token']}"})

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "4.0.0"
    assert "/api/users" in document["paths"]
    assert response.headers["Cache-Control"] == "no-store"
    assert issued.json()["token"] not in response.text
    assert document == openapi_document("4.0.0").to_data()


def test_admin_contract_refuses_missing_invalid_and_revoked_tokens(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, httpx.Client(base_url=url) as client:
        issued = client.post(
            "/api/tokens",
            json={"name": "integration", "scope": "read"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        ).json()
        assert (
            client.delete(f"/api/tokens/{issued['id']}", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}).status_code
            == 200
        )
        for token in ("", "not-a-token", issued["token"]):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = client.get("/api/openapi.json", headers=headers)
            assert response.status_code == 401
            error = ApiErrorResponse.model_validate_json(response.content)
            assert error.error.code == "unauthenticated"


def test_every_server_route_has_a_catalog_entry_or_an_internal_reason() -> None:
    actual = {(method, pattern.pattern) for method, pattern, _scope, _handler in ALL_ROUTES}
    cataloged = {(item.method, path_pattern(item.path).pattern) for item in API_ROUTES}
    internal = {(item.method, item.pattern.pattern) for item in INTERNAL_ROUTES}
    assert actual == cataloged | internal | set(INTERNAL_UI_ROUTES)
    assert all(item.reason for item in INTERNAL_ROUTES)
    assert all(INTERNAL_UI_ROUTES.values())
    assert all(not path.startswith("/api/") for _method, path in INTERNAL_UI_ROUTES)
    assert len(actual) == len(ALL_ROUTES)

    document = openapi_document().to_data()
    assert document["info"]["version"] == __version__
    published = {(method.upper(), path) for path, operations in document["paths"].items() for method in operations}
    assert published == {(item.method, item.path) for item in API_ROUTES if item.internal_reason is None}
    for item in API_ROUTES:
        assert item.scope and item.summary and "\n" not in item.summary
        assert item.response_model is not None and item.errors
        if item.internal_reason is not None:
            assert item.internal_reason.strip()
            assert (item.method, item.path) not in published


def test_documented_scopes_and_models_are_the_dispatch_contract() -> None:
    document = openapi_document().to_data()
    for item in API_ROUTES:
        bound = [
            route
            for route in ALL_ROUTES
            if route[0] == item.method and route[1].pattern == path_pattern(item.path).pattern
        ]
        assert len(bound) == 1
        assert bound[0][2] == item.scope
        assert bound[0][3].__name__ == item.operation_id
        if item.internal_reason is not None:
            continue
        operation = document["paths"][item.path][item.method.lower()]
        assert operation["x-meridian-scopes"] == list(item.scopes)
        for held in TOKEN_SCOPES:
            assert any(scope_covers(held, needed) for needed in item.scopes) == any(
                scope_covers(held, needed) for needed in operation["x-meridian-scopes"]
            )
        assert operation["x-meridian-credentials"] is item.credentials
        assert operation["responses"]["200"]["x-meridian-credentials"] is item.credentials
        if item.credentials:
            assert CREDENTIAL_SENTENCE in operation["description"]
            assert CREDENTIAL_SENTENCE in operation["responses"]["200"]["description"]
        assert ("requestBody" in operation) is (item.request_model is not None)
        for error in item.errors:
            response = operation["responses"][str(error.status)]
            assert set(error.codes) <= set(response["x-meridian-error-codes"])
            assert (
                _resolve(document, response["content"]["application/json"]["schema"]["$ref"])["title"]
                == "ApiErrorResponse"
            )


def test_all_schema_and_discriminator_references_resolve_locally() -> None:
    document = openapi_document().to_data()
    for value in _objects(document):
        if "$ref" in value:
            assert _resolve(document, value["$ref"])
        for reference in value.get("discriminator", {}).get("mapping", {}).values():
            assert _resolve(document, reference)


def test_request_schemas_keep_pydantic_bounds_and_both_patch_forms() -> None:
    document = openapi_document().to_data()
    create = document["paths"]["/api/users"]["post"]
    schema = _resolve(document, create["requestBody"]["content"]["application/json"]["schema"]["$ref"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["traffic_limit_bytes"]["maximum"] == MAX_TRAFFIC_LIMIT_BYTES
    assert "username" in schema["required"]
    named = document["paths"]["/api/users/{username}"]["patch"]
    body = _resolve(document, named["requestBody"]["content"]["application/json"]["schema"]["$ref"])
    assert "username" not in body["properties"]
    collection = document["paths"]["/api/users"]["patch"]
    body = _resolve(document, collection["requestBody"]["content"]["application/json"]["schema"]["$ref"])
    assert "username" in body["required"]
    command = document["paths"]["/api/nodes/{node_id}/commands"]["post"]
    body = _resolve(document, command["requestBody"]["content"]["application/json"]["schema"]["$ref"])
    assert body["discriminator"]["propertyName"] == "kind"


def test_cut_off_scope_is_documented_and_checked_before_credentials_change(tmp_path: Path) -> None:
    document = openapi_document().to_data()
    operation = document["paths"]["/api/users/{username}/reissue"]["post"]
    assert operation["requestBody"]["required"] is False
    assert operation["x-meridian-conditional-scopes"] == [{"field": "cut_off", "equals": True, "scopes": ["admin"]}]
    with (
        serve_control(tmp_path) as url,
        httpx.Client(base_url=url, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as client,
    ):
        before = client.post("/api/users", json={"username": "member"}).json()
        token = client.post("/api/tokens", json={"name": "integration", "scope": "people"}).json()["token"]
        response = client.post(
            "/api/users/member/reissue", json={"cut_off": True}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_scope"
        assert client.get("/api/users/member").json()["uuid"] == before["uuid"]


def test_typed_list_deletion_and_error_responses_keep_their_wire_shapes(tmp_path: Path) -> None:
    by_id = {item.operation_id: item for item in API_ROUTES}
    with (
        serve_control(tmp_path) as url,
        httpx.Client(base_url=url, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as client,
    ):
        created = client.post("/api/users", json={"username": "member"})
        assert created.status_code == 200
        user = UserRecord.model_validate_json(created.content)
        listed = client.get("/api/users")
        assert listed.status_code == 200
        result = by_id["list_users"].response_model.model_validate_json(listed.content)
        assert result.model_dump(mode="json") == {"users": [user.model_dump(mode="json")]}
        invalid = client.patch("/api/users/member", json={"unknown": True})
        assert invalid.status_code == 400
        error = ApiErrorResponse.model_validate_json(invalid.content)
        assert error.error.code == "invalid_request"
        deleted = client.delete("/api/users/member")
        assert deleted.status_code == 200
        result = by_id["delete_user"].response_model.model_validate_json(deleted.content)
        assert result.model_dump(mode="json") == {"deleted": "member"}


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _resolve(document: dict[str, Any], reference: str) -> Any:
    assert reference.startswith("#/"), reference
    value = document
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def test_admin_openapi_is_exported_and_drift_checked(tmp_path: Path) -> None:
    output = tmp_path / "contracts"
    write_contracts(output)
    contract = output / "admin" / "openapi.json"
    assert contract.is_file()
    document = json.loads(contract.read_text(encoding="utf-8"))
    assert document["openapi"] == "3.1.0"
    assert check_contracts(output) == []

    document["paths"].pop("/api/users")
    contract.write_text(json.dumps(document), encoding="utf-8")
    assert "changed: admin/openapi.json" in check_contracts(output)


@pytest.mark.parametrize(("length", "routed"), [(48, True), (49, False)], ids=["longest-id", "one-too-long"])
def test_node_paths_keep_the_topology_id_length_bound(length: int, routed: bool) -> None:
    """The catalog once compiled `NodeId` without its maxLength, routing IDs the old table refused."""
    node = "a" * length
    assert bool(path_pattern("/api/nodes/{node_id}").fullmatch(f"/api/nodes/{node}")) is routed
    assert bool(path_pattern("/api/nodes/{node_id}/commands").fullmatch(f"/api/nodes/{node}/commands")) is routed
