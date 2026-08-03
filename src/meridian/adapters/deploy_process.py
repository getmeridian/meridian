"""Process adapter for running deploys from the localhost Engine."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from meridian.core.deploy import DeployRequest, DeployResult
from meridian.core.errors import EngineError
from meridian.core.models import ErrorCategory
from meridian.core.operations import DeployEventSink


def run_deploy_process(request: DeployRequest, operation: DeployEventSink) -> dict[str, Any]:
    """Run the stable CLI process API and stream JSONL events into Engine state."""
    with tempfile.TemporaryDirectory(prefix="meridian-engine-") as tmp:
        request_path = Path(tmp) / "deploy.json"
        request_path.write_text(request.model_dump_json(by_alias=True), encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "meridian",
                "deploy",
                "--request",
                str(request_path),
                "--json",
                "--events=jsonl",
            ],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        stderr_tail: list[str] = []
        if process.stderr is not None:
            for line in process.stderr:
                text = line.strip()
                if not text:
                    continue
                _capture_event(text, operation, stderr_tail)
        stdout = process.stdout.read() if process.stdout is not None else ""
        return_code = process.wait()

    envelope = _parse_envelope(stdout)
    if return_code != 0 or envelope.get("status") == "failed":
        raise _engine_error_from_envelope(envelope, stderr_tail)

    try:
        result = DeployResult.model_validate(envelope.get("data") or {})
    except ValidationError as exc:
        raise EngineError(
            "Deploy finished but returned an unexpected result.",
            hint=str(exc),
            category="bug",
        ) from exc

    return {
        "schema": "meridian.deploy-operation-result/v1",
        "envelope": envelope,
        "result": result.model_dump(mode="json"),
    }


def _capture_event(text: str, operation: DeployEventSink, stderr_tail: list[str]) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        stderr_tail.append(text)
        del stderr_tail[:-8]
        return
    if isinstance(payload, dict) and payload.get("schema") == "meridian.event/v1":
        operation.add_event(payload)
        return
    stderr_tail.append(text)
    del stderr_tail[:-8]


def _parse_envelope(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineError(
            "Deploy returned output Studio could not read.",
            hint=text[:500],
            category="bug",
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _engine_error_from_envelope(envelope: dict[str, Any], stderr_tail: list[str]) -> EngineError:
    errors = envelope.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            category: ErrorCategory = "system"
            if first.get("category") in {"user", "system", "bug"}:
                category = cast(ErrorCategory, first["category"])
            return EngineError(
                str(first.get("message") or "Deploy failed."),
                hint=str(first.get("hint") or ""),
                category=category,
            )
    summary = envelope.get("summary")
    if isinstance(summary, dict) and summary.get("text"):
        return EngineError(str(summary["text"]), hint="\n".join(stderr_tail), category="system")
    return EngineError("Deploy failed.", hint="\n".join(stderr_tail), category="system")
