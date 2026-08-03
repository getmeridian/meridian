"""Tests for meridian-core output contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from meridian.core.models import MeridianError, OutputEnvelope, ResourceRef, Summary
from meridian.core.output import EventStream, OperationTimer, envelope, json_dumps, jsonl_dumps
from meridian.core.redaction import REDACTED, redact


def test_output_envelope_has_stable_top_level_contract() -> None:
    timer = OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test")
    payload = envelope(
        command="fleet.inventory",
        data={"nodes": []},
        summary=Summary(text="0 nodes", changed=False, counts={"nodes": 0}),
        timer=timer,
    )

    data = json.loads(json_dumps(payload))

    assert data["schema"] == "meridian.output/v1"
    assert data["command"] == "fleet.inventory"
    assert data["operation_id"] == "op-test"
    assert data["started_at"] == "2026-05-04T21:00:00Z"
    assert data["status"] == "ok"
    assert data["exit_code"] == 0
    assert data["summary"] == {"changed": False, "counts": {"nodes": 0}, "text": "0 nodes"}
    assert data["data"] == {"nodes": []}
    assert data["warnings"] == []
    assert data["errors"] == []


def test_output_envelope_serializes_errors() -> None:
    err = MeridianError(
        code="MERIDIAN_INPUT_REQUIRED",
        category="user",
        message="Missing server IP",
        hint="Pass --server-ip.",
        exit_code=2,
    )
    payload = envelope(
        command="deploy",
        summary="Missing input",
        status="failed",
        exit_code=2,
        errors=[err],
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
    )

    data = json.loads(json_dumps(payload))

    assert data["status"] == "failed"
    assert data["errors"][0]["code"] == "MERIDIAN_INPUT_REQUIRED"
    assert data["errors"][0]["category"] == "user"
    assert data["errors"][0]["hint"] == "Pass --server-ip."


def test_output_envelope_is_validated_and_schema_exportable() -> None:
    payload = envelope(
        command="plan",
        summary="Plan has changes",
        status="changed",
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
    )
    schema = OutputEnvelope.model_json_schema()

    assert "schema" in schema["properties"]
    assert schema["properties"]["status"]["enum"] == ["ok", "changed", "no_changes", "failed", "cancelled"]
    with pytest.raises(ValidationError):
        OutputEnvelope.model_validate({**payload.model_dump(), "status": "surprising"})


def test_emit_json_redacts_sensitive_keys_and_text() -> None:
    data = json.loads(
        json_dumps(
            {
                "api_token": "secret-token",
                "secretKey": "camel-secret",
                "message": (
                    "password=hunter2 jwt=eyJaaaaaaaa.eyJbbbbbbbb.eyJcccccccc "
                    "Authorization: Bearer opaque-token X-API-Key: opaque-key secretKey=abc123 "
                    "panel=https://198.51.100.1/secret-panel/api"
                ),
                "nested": {
                    "database_url": "postgres://user:pass@example/db",
                    "apiKey": "opaque",
                    "token": "plain-token",
                    "sessionToken": "session-secret",
                    "subscription_url": "https://198.51.100.1/sub/abc123",
                },
            }
        )
    )

    assert data["api_token"] == REDACTED
    assert data["secretKey"] == REDACTED
    assert "hunter2" not in data["message"]
    assert "eyJaaaaaaaa" not in data["message"]
    assert "opaque-token" not in data["message"]
    assert "opaque-key" not in data["message"]
    assert "abc123" not in data["message"]
    assert "secret-panel" not in data["message"]
    assert data["nested"]["database_url"] == REDACTED
    assert data["nested"]["apiKey"] == REDACTED
    assert data["nested"]["token"] == REDACTED
    assert data["nested"]["sessionToken"] == REDACTED
    assert data["nested"]["subscription_url"] == REDACTED


def test_redact_string_handles_url_userinfo() -> None:
    assert redact("postgres://user:pass@example.org/db") == f"postgres://{REDACTED}@example.org/db"


def test_json_dumps_redacts_unknown_object_stringification() -> None:
    class SecretObject:
        def __str__(self) -> str:
            return "Authorization: Bearer opaque-token"

    data = json.loads(json_dumps({"value": SecretObject()}))

    assert data["value"] == f"Authorization: Bearer {REDACTED}"


def test_redaction_preserves_json_schema_property_definitions() -> None:
    data = json.loads(
        json_dumps(
            {
                "type": "object",
                "properties": {
                    "subscription_url": {"type": "string"},
                    "share_url": {"type": "string"},
                },
            }
        )
    )

    assert data["properties"]["subscription_url"] == {"type": "string"}
    assert data["properties"]["share_url"] == {"type": "string"}


def test_redaction_does_not_treat_arbitrary_properties_as_schema() -> None:
    data = json.loads(
        json_dumps(
            {
                "properties": {
                    "api_token": "secret-token",
                    "subscription_url": "https://198.51.100.1/sub/abc123",
                }
            }
        )
    )

    assert data["properties"]["api_token"] == REDACTED
    assert data["properties"]["subscription_url"] == REDACTED


def test_redaction_handles_json_style_secrets_inside_strings() -> None:
    data = json.loads(
        json_dumps(
            {
                "message": (
                    '{"api_token":"secret-token","subscription_url":"https://198.51.100.1/sub/abc123"} '
                    '{"token":"plain-token","sessionToken":"session-secret"} '
                    'Authorization: "Bearer opaque-token" X-API-Key: "opaque-key"'
                )
            }
        )
    )

    assert "secret-token" not in data["message"]
    assert "abc123" not in data["message"]
    assert "plain-token" not in data["message"]
    assert "session-secret" not in data["message"]
    assert "opaque-token" not in data["message"]
    assert "opaque-key" not in data["message"]


def test_redaction_preserves_boolean_secret_metadata() -> None:
    data = redact({"secret": False, "nested": {"secret": True}, "secret_key": "value"})

    assert data["secret"] is False
    assert data["nested"]["secret"] is True
    assert data["secret_key"] == REDACTED


def test_jsonl_events_are_monotonic_and_redacted() -> None:
    events = EventStream(operation_id="op-test")

    first = events.event(
        "provision.step.started",
        phase="provision",
        resource=ResourceRef(kind="node", id="198.51.100.1", name="exit-a"),
        message="Install Docker",
    )
    second = events.event(
        "ssh.command.completed",
        phase="ssh",
        data={"command": "curl -H 'Authorization: Bearer eyJaaaaaaaa.eyJbbbbbbbb.eyJcccccccc'"},
    )

    assert first.seq == 1
    assert second.seq == 2
    lines = [json.loads(jsonl_dumps(first)), json.loads(jsonl_dumps(second))]
    assert lines[0]["schema"] == "meridian.event/v1"
    assert lines[0]["operation_id"] == "op-test"
    assert lines[0]["seq"] == 1
    assert lines[1]["seq"] == 2
    assert "eyJaaaaaaaa" not in lines[1]["data"]["command"]


def test_jsonl_dumps_writes_one_compact_record() -> None:
    assert jsonl_dumps({"b": 2, "a": 1}) == '{"a":1,"b":2}'
