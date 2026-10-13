"""`meridian route add|list|remove` are exit intent edits plus a reviewed apply."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.cluster import ClusterConfig
from meridian.commands.route import run_add, run_list, run_remove
from meridian.console import set_json_mode
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    EgressOverrideIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.servers import ServerEntry, ServerRegistry


class _Runtime:
    def __init__(self, result: ResourceExecutionResult) -> None:
        self.result = result
        self.applied: list[SetupIntent] = []

    def apply_intent(self, intent: SetupIntent) -> ResourceExecutionResult:
        self.applied.append(intent)
        return self.result


@pytest.fixture
def registry(servers_file: Path) -> ServerRegistry:
    saved = ServerRegistry(servers_file)
    saved.add(ServerEntry(host="198.51.100.11", name="exit-host", id="srv-exit"))
    return saved


@pytest.fixture(autouse=True)
def _json_off():
    yield
    set_json_mode(False)


def _intent(overrides: list[EgressOverrideIntent] | None = None) -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-exit"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
                egress_overrides=overrides or [],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["alice"]),
    )


def _run(call, intent: SetupIntent, runtime: _Runtime | None = None, **patches) -> int:
    cluster = ClusterConfig(topology_intent=intent)
    runtime = runtime or _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))
    with (
        patch("meridian.commands.route.load_topology_cluster", return_value=cluster),
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.commands.route.confirm", return_value=patches.get("confirmed", True)),
    ):
        try:
            call()
        except typer.Exit as exc:
            return int(exc.exit_code)
    return 0


_AI = EgressOverrideIntent(id="ai", match="domain", values=["example.com"], action="warp")


def test_add_by_saved_server_name_applies_the_edited_exit(registry: ServerRegistry) -> None:
    runtime = _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

    assert _run(lambda: run_add("exit-host", ["Example.com"], "warp", "ai"), _intent(), runtime) == 0

    (applied,) = runtime.applied
    assert applied.exits[0].egress_overrides == [_AI]


def test_add_json_envelope_carries_the_new_override(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    set_json_mode(True)

    assert _run(lambda: run_add("exit-a", ["geosite:netflix"], "direct"), _intent()) == 0

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "route.add"
    assert envelope["data"] == {
        "exit": "exit-a",
        "override": {"id": "direct", "match": "geosite", "values": ["netflix"], "action": "direct"},
    }


def test_unknown_via_and_unknown_exit_fail_before_apply(registry: ServerRegistry) -> None:
    runtime = _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

    assert _run(lambda: run_add("exit-a", ["example.com"], "tor"), _intent(), runtime) == 2
    assert _run(lambda: run_add("exit-z", ["example.com"], "warp"), _intent(), runtime) == 2
    assert runtime.applied == []


def test_failed_apply_is_a_system_failure(registry: ServerRegistry) -> None:
    failed = _Runtime(
        ResourceExecutionResult(plan_hash="sha256:plan", generation=2, retirement_errors=["profile: panel down"])
    )

    assert _run(lambda: run_add("exit-a", ["example.com"], "block"), _intent(), failed) == 3


def test_list_json_reports_every_exit_in_order(registry: ServerRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    set_json_mode(True)

    assert _run(lambda: run_list(), _intent([_AI])) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    assert data == {
        "exits": [
            {
                "exit": "exit-a",
                "warp": False,
                "overrides": [{"id": "ai", "match": "domain", "values": ["example.com"], "action": "warp"}],
            }
        ]
    }


def test_remove_applies_without_the_override(registry: ServerRegistry) -> None:
    runtime = _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

    assert _run(lambda: run_remove("198.51.100.11", "ai"), _intent([_AI]), runtime) == 0

    assert runtime.applied[0].exits[0].egress_overrides == []


def test_declined_removal_changes_nothing(registry: ServerRegistry) -> None:
    runtime = _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

    assert _run(lambda: run_remove("exit-a", "ai"), _intent([_AI]), runtime, confirmed=False) == 1
    assert runtime.applied == []


def test_json_removal_requires_yes(registry: ServerRegistry) -> None:
    set_json_mode(True)
    runtime = _Runtime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

    assert _run(lambda: run_remove("exit-a", "ai"), _intent([_AI]), runtime) == 2
    assert _run(lambda: run_remove("exit-a", "ai", yes=True), _intent([_AI]), runtime) == 0
