"""A stalled SSH connection is redialed and read again; an uncertain mutation is observed, never repeated blind."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from meridian.cluster import ClusterConfig
from meridian.core.errors import MeridianError
from meridian.reconciler.resource_executor import execute_resource_plan, inspect_resource_plan
from meridian.reconciler.resources import ResourceReconcileError, UnknownResourceOutcome
from meridian.reconciler.server_drivers import FirewallRuleDriver, ServerDriverContext
from meridian.reconciler.stall_retry import StallRetry, is_transient
from tests.support.reporters import CaptureReporter
from tests.support.topology import firewall_resource, resource_plan


def _stalled() -> ResourceReconcileError:
    return ResourceReconcileError("evidence is unavailable (inspection timed out)", retryable=True)


class _Calls:
    """A callable that raises each queued error once, then returns ``"done"``."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        if self.errors:
            raise self.errors.pop(0)
        return "done"


# --- StallRetry -------------------------------------------------------------


def test_transient_failure_is_retried_after_each_pause_and_reported() -> None:
    pauses: list[float] = []
    notices: list[tuple[int, float]] = []
    call = _Calls(_stalled(), _stalled())
    retry = StallRetry(delays=(2.0, 5.0, 10.0), sleep=pauses.append)

    assert retry.call(call, lambda attempt, delay, _exc: notices.append((attempt, delay))) == "done"
    assert call.count == 3 and pauses == [2.0, 5.0] and notices == [(1, 2.0), (2, 5.0)]


@pytest.mark.parametrize(
    "error",
    [
        UnknownResourceOutcome("Timed out while mutating firewall:x; observation is required."),
        ResourceReconcileError("a required inspection tool is missing"),
        MeridianError("Another apply is running", category="user", retryable=True),
        RuntimeError("driver bug"),
    ],
    ids=["uncertain-mutation", "not-retryable", "user-error", "not-meridian"],
)
def test_only_retryable_system_failures_are_retried(error: Exception) -> None:
    call = _Calls(error)
    with pytest.raises(type(error)):
        StallRetry(delays=(0.0,), sleep=lambda _s: None).call(call)
    assert call.count == 1 and not is_transient(error)


def test_outage_that_outlasts_the_schedule_stops_retrying_for_the_run() -> None:
    retry = StallRetry(delays=(0.0, 0.0), sleep=lambda _s: None)
    first = _Calls(*[_stalled()] * 5)
    with pytest.raises(ResourceReconcileError):
        retry.call(first)
    later = _Calls(_stalled())
    with pytest.raises(ResourceReconcileError):
        retry.call(later)
    assert (first.count, later.count) == (3, 1)


def test_cancellation_stops_retrying() -> None:
    call = _Calls(_stalled(), _stalled())
    with pytest.raises(ResourceReconcileError):
        StallRetry(delays=(0.0, 0.0), sleep=lambda _s: None, stop=lambda: True).call(call)
    assert call.count == 1


# --- a whole apply through a killed connection ------------------------------


class UfwHost:
    """A server whose UFW honours ``show added`` and ``allow``, and whose SSH connection can be killed.

    ``kills`` queues what happens to the next command starting with a prefix:
    ``"stall"`` times out before it runs (124), ``"drop"`` loses the connection
    before it runs (255), ``"landed"`` runs it and then loses the connection (255).
    """

    def __init__(self, **kills: list[str]) -> None:
        self.rules: list[str] = []
        self.commands: list[str] = []
        self.kills = {prefix.replace("_", " "): list(modes) for prefix, modes in kills.items()}
        self.user = "root"

    def run(self, command: str, timeout: int = 30, **_kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        mode = next((modes.pop(0) for prefix, modes in self.kills.items() if command.startswith(prefix) and modes), "")
        if mode == "stall":
            return SimpleNamespace(returncode=124, stdout="", stderr=f"Command timed out after {timeout}s")
        if mode == "drop":
            return SimpleNamespace(returncode=255, stdout="", stderr="Connection reset by peer")
        result = self._execute(command)
        if mode == "landed":
            return SimpleNamespace(returncode=255, stdout="", stderr="client_loop: send disconnect: Broken pipe")
        return result

    def _execute(self, command: str) -> SimpleNamespace:
        if command.startswith("ufw show added"):
            listing = "".join(f"ufw {rule}\n" for rule in self.rules)
            return SimpleNamespace(returncode=0, stdout=f"Added user rules:\n{listing}", stderr="")
        if command.startswith("ufw allow"):
            self.rules.append(command.removeprefix("ufw "))
            return SimpleNamespace(returncode=0, stdout="Rule added\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def mutations(self) -> list[str]:
        return [command for command in self.commands if command.startswith("ufw allow")]


def _apply(host: UfwHost, *ports: int) -> tuple[Any, CaptureReporter]:
    plan = resource_plan(*(firewall_resource(port) for port in ports or (443,)))
    context = ServerDriverContext(
        plan=plan,
        cluster=ClusterConfig(),
        connection_for=lambda _ref: host,  # type: ignore[arg-type,return-value]
        server_addresses={"srv-exit": "198.51.100.10"},
    )
    reporter = CaptureReporter()
    result = execute_resource_plan(
        plan,
        ClusterConfig(),
        {"firewall_rule": FirewallRuleDriver(context)},
        persist=lambda _state: None,
        reporter=reporter,
    )
    return result, reporter


def _stall_warnings(reporter: CaptureReporter) -> list[Any]:
    return [event for event in reporter.events if event.data.get("code") == "connection_stalled"]


def test_stalled_observation_redials_and_converges() -> None:
    host = UfwHost(ufw_show=["stall", "drop"])
    result, reporter = _apply(host)

    assert result.all_succeeded and [r.status for r in result.results] == ["applied"]
    assert len(host.mutations()) == 1
    warnings = _stall_warnings(reporter)
    assert [event.data["attempt"] for event in warnings] == [1, 2]
    assert warnings[0].type == "warning" and warnings[0].message == "connection stalled, retrying"
    assert warnings[0].resource is not None and warnings[0].resource.id == "firewall:srv-exit:tcp:443"


def test_connection_killed_mid_apply_converges_and_never_repeats_a_landed_mutation() -> None:
    # The first rule's mutation lands but loses its connection, and the connection is still
    # down for the next two reads. The second rule's apply stalls on its own follow-up read,
    # which makes that mutation uncertain too: it is observed, not run again.
    host = UfwHost(ufw_allow=["landed"], ufw_show=["", "stall", "drop", "", "", "stall"])
    result, reporter = _apply(host, 443, 8443)

    assert result.all_succeeded
    assert [r.status for r in result.results] == ["applied", "applied"]
    assert len(host.mutations()) == 2 and len(set(host.mutations())) == 2  # one per rule, none repeated
    assert len(_stall_warnings(reporter)) == 2


def test_uncertain_mutation_that_did_not_land_is_reapplied_only_after_observation() -> None:
    host = UfwHost(ufw_allow=["drop"])
    result, _reporter = _apply(host)

    assert result.all_succeeded
    kinds = ["allow" if command.startswith("ufw allow") else "show" for command in host.commands]
    assert kinds[:4] == ["show", "allow", "show", "allow"]  # observed absent between the two attempts


def test_read_only_plan_rides_out_a_stalled_observation() -> None:
    host = UfwHost(ufw_show=["drop"])
    plan = resource_plan(firewall_resource(443))
    context = ServerDriverContext(
        plan=plan,
        cluster=ClusterConfig(),
        connection_for=lambda _ref: host,  # type: ignore[arg-type,return-value]
        server_addresses={"srv-exit": "198.51.100.10"},
    )
    inspection = inspect_resource_plan(plan, ClusterConfig(), {"firewall_rule": FirewallRuleDriver(context)})

    [item] = inspection.inspections
    assert item.error == "" and item.observation is not None and host.mutations() == []
