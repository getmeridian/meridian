"""`node replace` moves one exit to another server as a reviewed intent edit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands.node_replace import run_replace
from meridian.core.topology import ProtocolPathIntent, SetupIntent
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.servers import ServerEntry, ServerRegistry
from tests.test_topology_removal_commands import SERVERS, _intent


class _Runtime:
    def __init__(self, result: ResourceExecutionResult | None = None) -> None:
        self.result = result or ResourceExecutionResult(plan_hash="sha256:plan", generation=2, retired=["a"])
        self.applied: tuple[SetupIntent, str] | None = None

    def review(self, draft: object, *, cluster: object) -> SimpleNamespace:
        retiring = (
            ManagedResourceBinding(
                logical_id="node:exit-b", resource_kind="node_runtime", active=True, server_ref="srv-exit-b"
            ),
        )
        return SimpleNamespace(retiring=retiring, review_hash="sha256:review")

    def apply_intent(self, intent: SetupIntent, *, expected_plan_hash: str) -> ResourceExecutionResult:
        self.applied = (intent, expected_plan_hash)
        return self.result


@pytest.fixture
def registry(servers_file: Path) -> ServerRegistry:
    saved = ServerRegistry(servers_file)
    for server_id, (host, name) in {**SERVERS, "srv-spare": ("198.51.100.30", "spare")}.items():
        saved.add(ServerEntry(host=host, name=name, id=server_id))
    return saved


def _replace(
    old: str, new: str, *, intent: SetupIntent | None = None, runtime: _Runtime | None = None, name: str = ""
) -> tuple[int, _Runtime, MagicMock]:
    runtime = runtime or _Runtime()
    host = new.rpartition("@")[2]
    target = SimpleNamespace(
        ip=host, user="root", port=22, local_mode=False, machine_id="", conn=SimpleNamespace(identity_file="")
    )
    with (
        patch(
            "meridian.commands.node_replace.load_topology_cluster",
            return_value=ClusterConfig(topology_intent=intent or _intent()),
        ),
        patch("meridian.commands.resolve.resolve_server", return_value=target),
        patch(
            "meridian.commands.resolve.ensure_server_connection", side_effect=lambda resolved, **_: resolved
        ) as connect,
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
    ):
        try:
            run_replace(old, new, name=name, yes=True)
        except typer.Exit as exc:
            return int(exc.exit_code), runtime, connect
    return 0, runtime, connect


def test_the_exit_moves_with_its_id_paths_and_relays(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    code, runtime, _connect = _replace("exit-b-host", "198.51.100.20", name="exit-b-new")

    assert code == 0 and runtime.applied is not None
    updated, approved = runtime.applied
    new_entry = registry.find("198.51.100.20")
    assert new_entry is not None and new_entry.name == "exit-b-new"
    moved = next(exit_ for exit_ in updated.exits if exit_.id == "exit-b")
    assert moved.server_ref == new_entry.id
    assert moved.paths == next(exit_ for exit_ in _intent().exits if exit_.id == "exit-b").paths
    assert updated.transparent_relays == _intent().transparent_relays
    assert approved == "sha256:review"
    output = capsys.readouterr().err
    assert "Relays re-pointed to the new server: relay-a" in output
    assert "remove node:exit-b (exit-b-host)" in output


def test_a_saved_spare_server_keeps_its_title(registry: ServerRegistry) -> None:
    code, runtime, _connect = _replace("exit-b", "198.51.100.30")

    assert code == 0 and runtime.applied is not None
    spare = registry.find("198.51.100.30")
    assert spare is not None and spare.name == "spare"
    assert next(exit_ for exit_ in runtime.applied[0].exits if exit_.id == "exit-b").server_ref == "srv-spare"


@pytest.mark.parametrize("old", ["198.51.100.10", "control", "exit-a"])
def test_the_control_server_cannot_be_moved(registry: ServerRegistry, old: str) -> None:
    intent = _intent()
    on_control = intent.model_copy(
        update={
            "exits": [
                exit_.model_copy(update={"server_ref": "srv-control"}) if exit_.id == "exit-a" else exit_
                for exit_ in intent.exits
            ]
        }
    )

    code, runtime, connect = _replace(old, "198.51.100.20", intent=on_control)

    assert code == 2 and runtime.applied is None
    connect.assert_not_called()


@pytest.mark.parametrize("new", ["198.51.100.11", "198.51.100.13", "198.51.100.15"])
def test_a_server_that_already_has_a_role_is_refused_before_connecting(registry: ServerRegistry, new: str) -> None:
    code, runtime, connect = _replace("exit-b", new)

    assert code == 2 and runtime.applied is None
    connect.assert_not_called()


@pytest.mark.parametrize("old", ["exit-zz", "gateway"])
def test_only_exits_can_be_replaced(registry: ServerRegistry, old: str) -> None:
    code, runtime, _connect = _replace(old, "198.51.100.20")

    assert code == 2 and runtime.applied is None


def test_an_unfinished_move_is_a_system_failure_that_points_to_apply(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = ResourceExecutionResult(
        plan_hash="sha256:plan", generation=2, retirement_errors=["retire node:exit-b: SSH unreachable"]
    )

    code, _runtime, _connect = _replace("exit-b", "198.51.100.20", runtime=_Runtime(failed))

    assert code == 3
    assert "meridian apply" in capsys.readouterr().err


def test_bare_ip_paths_take_the_new_address_and_domains_stay(registry: ServerRegistry) -> None:
    """The moved exit kept the old IP as certificate name, so the new server could never get a certificate."""
    payload = _intent().model_dump(mode="json")
    extra = [
        ProtocolPathIntent(id="xhttp-b", protocol="xhttp", tls_sni="198.51.100.12", host="198.51.100.12", path="/xh"),
        ProtocolPathIntent(id="hy2-b", protocol="hysteria2", tls_sni="198.51.100.12"),
        ProtocolPathIntent(id="wss-b", protocol="wss", tls_sni="vpn.example.com", host="vpn.example.com", path="/ws"),
    ]
    next(exit_ for exit_ in payload["exits"] if exit_["id"] == "exit-b")["paths"] += [
        path.model_dump(mode="json") for path in extra
    ]

    code, runtime, _connect = _replace("exit-b", "198.51.100.20", intent=SetupIntent.model_validate(payload))

    assert code == 0 and runtime.applied is not None
    moved = {path.id: path for exit_ in runtime.applied[0].exits if exit_.id == "exit-b" for path in exit_.paths}
    assert (moved["xhttp-b"].tls_sni, moved["xhttp-b"].host) == ("198.51.100.20", "198.51.100.20")
    assert moved["hy2-b"].tls_sni == "198.51.100.20"
    assert (moved["wss-b"].tls_sni, moved["wss-b"].host) == ("vpn.example.com", "vpn.example.com")
    assert moved["reality-b"].reality_sni == "www.example.org"


def test_rerun_on_the_current_server_repairs_a_stale_bare_ip_path(registry: ServerRegistry) -> None:
    """A pre-b9 move left the old server's IP on the exit; rerunning the same move repairs it."""
    payload = _intent().model_dump(mode="json")
    exit_b = next(exit_ for exit_ in payload["exits"] if exit_["id"] == "exit-b")
    exit_b["server_ref"] = "srv-spare"
    exit_b["paths"].append(
        ProtocolPathIntent(id="hy2-b", protocol="hysteria2", tls_sni="198.51.100.12").model_dump(mode="json")
    )

    code, runtime, _connect = _replace("exit-b", "198.51.100.30", intent=SetupIntent.model_validate(payload))

    assert code == 0 and runtime.applied is not None
    moved = next(exit_ for exit_ in runtime.applied[0].exits if exit_.id == "exit-b")
    assert [path.tls_sni for path in moved.paths if path.id == "hy2-b"] == ["198.51.100.30"]


def test_rerun_on_the_current_server_without_stale_paths_is_refused(registry: ServerRegistry) -> None:
    payload = _intent().model_dump(mode="json")
    next(exit_ for exit_ in payload["exits"] if exit_["id"] == "exit-b")["server_ref"] = "srv-spare"

    code, runtime, _connect = _replace("exit-b", "198.51.100.30", intent=SetupIntent.model_validate(payload))

    assert code == 2 and runtime.applied is None
