"""Process API regressions for V4 compiler-backed plan and apply."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import ReviewedApplyPersistenceError
from meridian.commands.apply import run as run_apply_command
from meridian.commands.plan import run as run_plan_command
from meridian.commands.v4_topology import run_v4_apply, run_v4_plan
from meridian.compiler import TopologyCompileError
from meridian.console import set_json_mode
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)

_PLAN_HASH = "a" * 64


def _cluster() -> ClusterConfig:
    return ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-exit",
                    paths=[
                        ProtocolPathIntent(
                            id="reality",
                            protocol="reality",
                            reality_sni="www.microsoft.com",
                        )
                    ],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        )
    )


def _resource(logical_id: str = "firewall:srv-exit:443") -> SimpleNamespace:
    return SimpleNamespace(
        logical_id=logical_id,
        payload=SimpleNamespace(kind="firewall_rule"),
        desired_hash="b" * 64,
        dependencies=[],
    )


def _action_result(
    *,
    status: str,
    changed: bool,
    error: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        action=SimpleNamespace(resource=_resource()),
        status=status,
        succeeded=status in {"converged", "applied"},
        changed=changed,
        error=error,
    )


def _execution(*results: SimpleNamespace) -> SimpleNamespace:
    items = list(results)
    return SimpleNamespace(
        plan_hash=_PLAN_HASH,
        generation=4,
        results=items,
        all_succeeded=all(item.succeeded for item in items),
        changed=any(item.changed for item in items),
        failed=[item for item in items if not item.succeeded],
    )


def _runtime(result: SimpleNamespace) -> MagicMock:
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(plan=SimpleNamespace(plan_hash=_PLAN_HASH, resources=[_resource()]))
    runtime.apply_intent.return_value = result
    return runtime


def _run_apply_json(result: SimpleNamespace, *, yes: bool = True) -> tuple[dict, int | None, MagicMock]:
    runtime = _runtime(result)
    output = io.StringIO()
    exit_code = None
    with (
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.servers.ServerRegistry"),
        redirect_stdout(output),
    ):
        try:
            run_v4_apply(
                _cluster(),
                yes=yes,
                json_output=True,
                operation=OperationContext(),
            )
        except typer.Exit as exc:
            exit_code = exc.exit_code
    return json.loads(output.getvalue()), exit_code, runtime


def test_v4_apply_json_noop_uses_typed_compiled_result() -> None:
    payload, exit_code, runtime = _run_apply_json(_execution(_action_result(status="converged", changed=False)))

    assert exit_code is None
    assert payload["status"] == "no_changes"
    assert payload["exit_code"] == 0
    assert payload["data"]["plan_hash"] == _PLAN_HASH
    assert payload["data"]["generation"] == 4
    assert payload["data"]["all_succeeded"] is True
    assert payload["data"]["counts"] == {"actions": 1, "succeeded": 1, "failed": 0, "skipped": 0}
    assert payload["data"]["actions"][0]["status"] == "converged"
    runtime.apply_intent.assert_called_once_with(
        _cluster().topology_intent,
        expected_plan_hash=_PLAN_HASH,
    )


def test_v4_apply_json_partial_failure_is_one_terminal_envelope() -> None:
    payload, exit_code, _runtime_mock = _run_apply_json(
        _execution(
            _action_result(status="applied", changed=True),
            _action_result(status="failed", changed=False, error="firewall reload failed"),
        )
    )

    assert exit_code == 3
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 3
    assert payload["data"]["changed"] is True
    assert payload["data"]["all_succeeded"] is False
    assert payload["data"]["counts"] == {"actions": 2, "succeeded": 1, "failed": 1, "skipped": 0}
    assert payload["errors"][0]["code"] == "MERIDIAN_APPLY_FAILED"


def test_v4_apply_json_confirmation_returns_typed_preview() -> None:
    payload, exit_code, _runtime_mock = _run_apply_json(_execution(), yes=False)

    assert exit_code == 2
    assert payload["status"] == "failed"
    assert payload["data"] == {"plan_hash": _PLAN_HASH, "resource_count": 1}
    assert payload["errors"][0]["code"] == "MERIDIAN_CONFIRMATION_REQUIRED"


@pytest.mark.parametrize("command", ["apply", "plan"])
@pytest.mark.parametrize(
    "error",
    [
        LocalStateError("Saved panel credentials are missing."),
        TopologyCompileError("The reviewed topology cannot be represented."),
    ],
)
def test_v4_typed_exception_emits_one_terminal_json_envelope(command: str, error: Exception) -> None:
    output = io.StringIO()
    target = f"meridian.commands.{command}._run_v4_{'topology' if command == 'apply' else 'plan'}"
    kwargs = {"yes": True, "json_output": True} if command == "apply" else {"json_output": True}
    runner = run_apply_command if command == "apply" else run_plan_command
    set_json_mode(True)
    try:
        with (
            patch("meridian.cluster.ClusterConfig.load", return_value=_cluster()),
            patch(target, side_effect=error),
            redirect_stdout(output),
            pytest.raises(typer.Exit) as exc_info,
        ):
            runner(**kwargs)
    finally:
        set_json_mode(False)

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 2
    assert payload["command"] == command
    assert payload["status"] == "failed"
    assert payload["data"] == {}
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["category"] == "user"


def test_v4_apply_persistence_failure_is_typed_system_error() -> None:
    runtime = _runtime(_execution())
    runtime.apply_intent.side_effect = ReviewedApplyPersistenceError(
        "disk full. Remote state may have changed; rerun plan and apply."
    )
    output = io.StringIO()
    set_json_mode(True)
    try:
        with (
            patch("meridian.cluster.ClusterConfig.load", return_value=_cluster()),
            patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
            patch("meridian.servers.ServerRegistry"),
            redirect_stdout(output),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_apply_command(yes=True, json_output=True)
    finally:
        set_json_mode(False)

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 3
    assert payload["status"] == "failed"
    assert payload["errors"][0]["category"] == "system"
    assert "remote state may have changed" in payload["errors"][0]["message"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"parallel": 8, "prune_extras": "ask"},
        {"parallel": 4, "prune_extras": "yes"},
    ],
)
def test_v4_apply_rejects_legacy_only_flags(kwargs: dict[str, object]) -> None:
    output = io.StringIO()
    with (
        patch("meridian.cluster.ClusterConfig.load", return_value=_cluster()),
        patch("meridian.commands.apply._run_v4_topology") as apply_v4,
        redirect_stdout(output),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_apply_command(yes=True, json_output=True, **kwargs)

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 2
    assert payload["status"] == "failed"
    assert "legacy apply options" in payload["errors"][0]["message"]
    apply_v4.assert_not_called()


def test_v4_plan_json_uses_typed_compiled_result() -> None:
    resource = _resource()
    inspection = SimpleNamespace(
        plan_hash=_PLAN_HASH,
        converged=True,
        drifted=[],
        inspections=[SimpleNamespace(action=SimpleNamespace(resource=resource), error="", converged=True)],
    )
    runtime = MagicMock()
    runtime.inspect_intent.return_value = inspection
    cluster = _cluster()
    cluster.active_plan_hash = _PLAN_HASH
    output = io.StringIO()

    with (
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.servers.ServerRegistry"),
        redirect_stdout(output),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_v4_plan(cluster, json_output=True, operation=OperationContext())

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 0
    assert payload["status"] == "no_changes"
    assert payload["data"]["plan_hash"] == _PLAN_HASH
    assert payload["data"]["converged"] is True
    assert payload["data"]["resources"] == [
        {
            "logical_id": "firewall:srv-exit:443",
            "kind": "firewall_rule",
            "desired_hash": "b" * 64,
            "dependencies": [],
        }
    ]


def test_v4_plan_reports_generation_activation_when_resources_match() -> None:
    resource = _resource()
    inspection = SimpleNamespace(
        plan_hash=_PLAN_HASH,
        converged=True,
        inspections=[SimpleNamespace(action=SimpleNamespace(resource=resource), error="", converged=True)],
    )
    runtime = MagicMock()
    runtime.inspect_intent.return_value = inspection
    cluster = _cluster()
    output = io.StringIO()

    with (
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.servers.ServerRegistry"),
        redirect_stdout(output),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_v4_plan(cluster, json_output=True, operation=OperationContext())

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 2
    assert payload["status"] == "changed"
    assert payload["data"]["drifted_resources"] == []
    assert payload["data"]["state_changes"] == ["activate this reviewed plan as the current generation"]
    assert "requires activation" in payload["summary"]["text"]


def test_v4_plan_observation_error_is_typed_inconclusive_failure() -> None:
    resource = _resource()
    inspection = SimpleNamespace(
        plan_hash=_PLAN_HASH,
        converged=False,
        inspections=[
            SimpleNamespace(
                action=SimpleNamespace(resource=resource),
                error="observation failed: panel unavailable",
                converged=False,
            )
        ],
    )
    runtime = MagicMock()
    runtime.inspect_intent.return_value = inspection
    output = io.StringIO()

    with (
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.servers.ServerRegistry"),
        redirect_stdout(output),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_v4_plan(_cluster(), json_output=True, operation=OperationContext())

    payload = json.loads(output.getvalue())
    assert exc_info.value.exit_code == 3
    assert payload["status"] == "failed"
    assert payload["data"]["exit_code"] == 3
    assert payload["errors"][0]["code"] == "MERIDIAN_PLAN_EVIDENCE_UNAVAILABLE"
    assert payload["data"]["drifted_resources"] == []
    assert payload["data"]["observation_errors"][0]["logical_id"] == resource.logical_id
