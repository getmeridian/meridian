"""The control server's apply fence: stale or foreign state copies refuse instead of undoing work."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    ResourcePlan,
)
from meridian.reconciler.fence import (
    LEASE_DURATION,
    FenceRecord,
    FleetFenceError,
    LocalFenceView,
    decide_acquire,
)
from meridian.reconciler.resource_executor import execute_resource_plan
from meridian.reconciler.resources import ResourceAction, ResourceApplyReceipt, ResourceObservation
from meridian.state_fence import FENCE_FILE, SSHApplyFence, apply_holder, holder_gone
from tests.support.topology import firewall_resource, resource_plan

_NOW = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
_FLEET = "11111111-1111-4111-8111-111111111111"


def _local(generation: int = 0, plan_hash: str = "", fleet_id: str = _FLEET, fresh: bool = False) -> LocalFenceView:
    return LocalFenceView(fleet_id=fleet_id, active_generation=generation, active_plan_hash=plan_hash, fresh=fresh)


def _decide(remote: FenceRecord | None, local: LocalFenceView, **kwargs: Any) -> FenceRecord:
    return decide_acquire(remote, local, holder="laptop:1", now=_NOW, new_fleet_id="new-fleet", **kwargs)


# --- decide_acquire ---------------------------------------------------------


def test_first_apply_creates_the_record_with_a_new_fleet_id_and_a_lease() -> None:
    record = _decide(None, _local(fleet_id="", fresh=True))
    assert record.fleet_id == "new-fleet"
    assert record.lease_holder == "laptop:1"
    assert datetime.fromisoformat(record.lease_expires_at) == _NOW + LEASE_DURATION


def test_pre_fence_fleet_records_its_current_generation() -> None:
    record = _decide(None, _local(generation=7, plan_hash="a" * 64, fleet_id=""))
    assert (record.fleet_id, record.generation, record.plan_hash) == ("new-fleet", 7, "a" * 64)


def test_copy_behind_the_server_is_refused() -> None:
    remote = FenceRecord(fleet_id=_FLEET, generation=5, plan_hash="b" * 64)
    with pytest.raises(FleetFenceError, match="Another machine changed this fleet"):
        _decide(remote, _local(generation=4, plan_hash="a" * 64))


def test_same_generation_with_a_different_plan_is_refused() -> None:
    remote = FenceRecord(fleet_id=_FLEET, generation=5, plan_hash="b" * 64)
    with pytest.raises(FleetFenceError, match="Another machine changed this fleet"):
        _decide(remote, _local(generation=5, plan_hash="a" * 64))


def test_matching_copy_takes_the_lease() -> None:
    remote = FenceRecord(fleet_id=_FLEET, generation=5, plan_hash="a" * 64)
    record = _decide(remote, _local(generation=5, plan_hash="a" * 64))
    assert (record.generation, record.lease_holder) == (5, "laptop:1")


def test_copy_ahead_heals_a_record_whose_commit_was_lost() -> None:
    remote = FenceRecord(fleet_id=_FLEET, generation=5, plan_hash="b" * 64)
    record = _decide(remote, _local(generation=6, plan_hash="a" * 64))
    assert (record.generation, record.plan_hash) == (6, "a" * 64)


def test_copy_without_a_fleet_id_adopts_the_servers() -> None:
    remote = FenceRecord(fleet_id=_FLEET, generation=5, plan_hash="a" * 64)
    record = _decide(remote, _local(generation=5, plan_hash="a" * 64, fleet_id=""))
    assert record.fleet_id == _FLEET


def test_foreign_fleet_is_refused() -> None:
    remote = FenceRecord(fleet_id="other", generation=1, plan_hash="b" * 64)
    with pytest.raises(FleetFenceError, match="different copy of the fleet"):
        _decide(remote, _local(generation=3, plan_hash="a" * 64))


def test_fresh_copy_replaces_a_fence_left_by_a_torn_down_fleet() -> None:
    remote = FenceRecord(fleet_id="old-fleet", generation=9, plan_hash="b" * 64)
    record = _decide(remote, _local(fresh=True))
    assert (record.fleet_id, record.generation, record.plan_hash) == (_FLEET, 0, "")


def test_live_lease_from_another_holder_is_refused_as_retryable() -> None:
    remote = FenceRecord(
        fleet_id=_FLEET, lease_holder="desktop:2", lease_expires_at=(_NOW + timedelta(minutes=5)).isoformat()
    )
    with pytest.raises(FleetFenceError, match="Another apply is running from desktop") as exc_info:
        _decide(remote, _local())
    assert exc_info.value.retryable


def test_expired_or_dead_holder_lease_does_not_block() -> None:
    expired = FenceRecord(
        fleet_id=_FLEET, lease_holder="desktop:2", lease_expires_at=(_NOW - timedelta(seconds=1)).isoformat()
    )
    assert _decide(expired, _local()).lease_holder == "laptop:1"
    live = FenceRecord(
        fleet_id=_FLEET, lease_holder="laptop:9", lease_expires_at=(_NOW + timedelta(minutes=5)).isoformat()
    )
    assert _decide(live, _local(), holder_gone=lambda holder: holder == "laptop:9").lease_holder == "laptop:1"


def test_holder_gone_only_for_dead_processes_on_this_machine() -> None:
    mine = apply_holder()
    assert holder_gone(mine) is False  # this process is alive
    host, node, _pid, token = mine.split(":")
    finished = subprocess.Popen(["true"])  # noqa: S607
    finished.wait()
    assert holder_gone(f"{host}:{node}:{finished.pid}:{token}") is True
    assert holder_gone(f"other-host:{node}:1:{token}") is False
    assert holder_gone("garbage") is False
    assert holder_gone(f"{host}:{node}:not-a-pid:{token}") is False


# --- SSHApplyFence storage --------------------------------------------------


class FenceHost:
    """In-memory control server that honours the read and compare-and-swap commands."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.swaps = 0
        self.swap_calls = 0
        self.before_swap: Any = None
        # Connection drops: the next N reads fail, and swaps fail before (lost) or after (landed) moving.
        self.dropped_reads = 0
        self.lost_swaps = 0
        self.landed_swaps = 0
        self.on_lost_swap: Any = None

    def run(self, command: str, timeout: int = 30, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command.startswith("flock"):
            self.swap_calls += 1
            if self.lost_swaps:
                self.lost_swaps -= 1
                if self.on_lost_swap is not None:
                    self.on_lost_swap()
                return subprocess.CompletedProcess([], 255, "", "Connection reset by peer")
            result = self._swap(command)
            if self.landed_swaps and result.returncode == 0:
                self.landed_swaps -= 1
                return subprocess.CompletedProcess([], 124, "", f"Command timed out after {timeout}s")
            return result
        if "sha256sum" in command:
            if self.dropped_reads:
                self.dropped_reads -= 1
                return subprocess.CompletedProcess([], 255, "", "Connection timed out during banner exchange")
            body = self.files.get(FENCE_FILE)
            out = "absent\n" if body is None else f"{_digest(body)}\n{body}"
            return subprocess.CompletedProcess([], 0, out, "")
        raise AssertionError(f"unexpected command: {command}")

    def put_text(self, path: str, text: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("mode") == "600" and kwargs.get("sensitive") is True
        self.files[path] = text
        return subprocess.CompletedProcess([], 0, "", "")

    def _swap(self, command: str) -> subprocess.CompletedProcess[str]:
        if self.before_swap is not None:
            hook, self.before_swap = self.before_swap, None
            hook()
        script = shlex.split(command)[-1]
        expected = re.search(r'\[ "\$cur" = (\S+) \]', script)
        staged = re.search(r"mv (\S+) ", script)
        assert expected and staged
        current = self.files.get(FENCE_FILE)
        if (current is None and expected.group(1) != "absent") or (
            current is not None and _digest(current) != expected.group(1)
        ):
            self.files.pop(staged.group(1), None)
            return subprocess.CompletedProcess([], 4, "", "")
        self.files[FENCE_FILE] = self.files.pop(staged.group(1))
        self.swaps += 1
        return subprocess.CompletedProcess([], 0, "", "")

    def record(self) -> dict[str, Any]:
        return json.loads(self.files[FENCE_FILE])


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fence(host: FenceHost, holder: str = "laptop:1", clock: Any = None, **kwargs: Any) -> SSHApplyFence:
    return SSHApplyFence(host, holder=holder, clock=clock or (lambda: _NOW), **kwargs)  # type: ignore[arg-type]


def test_ssh_fence_acquires_commits_and_leaves_no_lease() -> None:
    host = FenceHost()
    fence = _fence(host)
    fleet_id = fence.acquire(_local(fleet_id="", fresh=True))
    assert host.record()["lease_holder"] == "laptop:1"
    fence.commit(1, "a" * 64)
    fence.release()
    assert host.record() == {
        "fleet_id": fleet_id,
        "generation": 1,
        "plan_hash": "a" * 64,
        "lease_holder": "",
        "lease_expires_at": "",
    }
    assert [path for path in host.files if path != FENCE_FILE] == []


def test_ssh_fence_release_without_commit_keeps_the_generation() -> None:
    host = FenceHost()
    host.files[FENCE_FILE] = json.dumps({"fleet_id": _FLEET, "generation": 3, "plan_hash": "a" * 64})
    fence = _fence(host)
    fence.acquire(_local(generation=3, plan_hash="a" * 64))
    fence.release()
    assert host.record()["generation"] == 3 and host.record()["lease_holder"] == ""


def test_ssh_fence_refuses_when_another_machine_wrote_between_read_and_swap() -> None:
    host = FenceHost()
    host.before_swap = lambda: host.files.__setitem__(FENCE_FILE, json.dumps({"fleet_id": "raced"}))
    with pytest.raises(FleetFenceError, match="at the same moment"):
        _fence(host).acquire(_local(fleet_id="", fresh=True))
    assert json.loads(host.files[FENCE_FILE]) == {"fleet_id": "raced"}
    assert [path for path in host.files if path != FENCE_FILE] == []


def test_ssh_fence_renews_only_after_the_renew_interval() -> None:
    host = FenceHost()
    now = [_NOW]
    fence = _fence(host, clock=lambda: now[0])
    fence.acquire(_local(fleet_id="", fresh=True))
    fence.renew()
    assert host.swaps == 1
    now[0] = _NOW + timedelta(minutes=6)
    fence.renew()
    assert host.swaps == 2
    assert datetime.fromisoformat(host.record()["lease_expires_at"]) == now[0] + LEASE_DURATION


def test_second_machine_is_refused_while_the_first_holds_the_lease() -> None:
    host = FenceHost()
    _fence(host, holder="laptop:1").acquire(_local(fleet_id="", fresh=True))
    with pytest.raises(FleetFenceError, match="Another apply is running from laptop"):
        _fence(host, holder="desktop:2").acquire(_local(fleet_id=""))


def test_check_explains_a_refusal_without_writing() -> None:
    host = FenceHost()
    host.files[FENCE_FILE] = json.dumps({"fleet_id": _FLEET, "generation": 5, "plan_hash": "b" * 64})
    warning = _fence(host).check(_local(generation=4, plan_hash="a" * 64))
    assert (
        "Another machine changed this fleet" in warning
        and "Copy " in warning
        and "from the machine that applied last" in warning
    )
    assert _fence(host).check(_local(generation=5, plan_hash="b" * 64)) == ""
    assert host.swaps == 0 and list(host.files) == [FENCE_FILE]


def test_ensure_raises_the_same_refusal_acquire_would() -> None:
    host = FenceHost()
    host.files[FENCE_FILE] = json.dumps({"fleet_id": _FLEET, "generation": 5, "plan_hash": "b" * 64})
    with pytest.raises(FleetFenceError, match="Another machine changed this fleet") as exc_info:
        _fence(host).ensure(_local(generation=4, plan_hash="a" * 64))
    assert "from the machine that applied last" in exc_info.value.hint
    assert host.swaps == 0


def test_check_stays_quiet_when_the_fence_is_unreadable() -> None:
    host = FenceHost()
    host.files[FENCE_FILE] = "{not json"
    assert _fence(host).check(_local()) == ""


def test_unreadable_fence_is_a_system_error_with_guidance() -> None:
    host = FenceHost()
    host.files[FENCE_FILE] = "{not json"
    with pytest.raises(FleetFenceError, match="unreadable") as exc_info:
        _fence(host).acquire(_local())
    assert exc_info.value.category == "system"


def test_lost_commit_tells_the_operator_to_rerun() -> None:
    host = FenceHost()
    fence = _fence(host)
    fence.acquire(_local(fleet_id="", fresh=True))
    host.before_swap = lambda: host.files.__setitem__(FENCE_FILE, "{}")
    with pytest.raises(FleetFenceError, match="Applied and saved locally") as exc_info:
        fence.commit(1, "a" * 64)
    assert exc_info.value.retryable


# --- stalled connections ----------------------------------------------------


def _acquired(host: FenceHost, **kwargs: Any) -> SSHApplyFence:
    fence = _fence(host, **kwargs)
    fence.acquire(_local(fleet_id="", fresh=True))
    return fence


def test_fence_write_that_timed_out_after_landing_is_re_read_not_repeated() -> None:
    host = FenceHost()
    stalls: list[int] = []
    fence = _acquired(host, on_stall=lambda attempt, _delay, _exc: stalls.append(attempt))
    host.landed_swaps = 1
    fence.commit(1, "a" * 64)

    assert host.swap_calls == 2 and host.swaps == 2  # acquire plus one commit swap, never a second
    assert host.record()["generation"] == 1 and host.record()["lease_holder"] == ""
    assert stalls == [1]


def test_fence_write_lost_before_landing_swaps_again_after_re_reading() -> None:
    host = FenceHost()
    fence = _acquired(host)
    host.lost_swaps, host.dropped_reads = 1, 1  # the commit's swap drops, and so does the first re-read
    fence.commit(1, "a" * 64)

    assert host.swaps == 2 and host.record()["generation"] == 1
    assert [path for path in host.files if path != FENCE_FILE] == []


def test_fence_read_rides_out_a_stalled_connection() -> None:
    host = FenceHost()
    host.dropped_reads = 2
    _acquired(host)
    assert host.record()["lease_holder"] == "laptop:1"


def test_fence_another_machine_moved_during_the_stall_is_a_conflict_not_a_retry() -> None:
    host = FenceHost()
    fence = _acquired(host)
    host.lost_swaps = 1
    host.on_lost_swap = lambda: host.files.__setitem__(FENCE_FILE, json.dumps({"fleet_id": "raced"}))
    with pytest.raises(FleetFenceError, match="at the same moment"):
        fence.commit(1, "a" * 64)
    assert host.swap_calls == 2 and json.loads(host.files[FENCE_FILE]) == {"fleet_id": "raced"}


def test_fence_outage_that_outlasts_every_retry_fails_without_swapping_blind() -> None:
    host = FenceHost()
    fence = _acquired(host)
    host.lost_swaps, host.dropped_reads = 100, 100
    with pytest.raises(FleetFenceError, match="Applied and saved locally") as exc_info:
        fence.commit(1, "a" * 64)
    assert exc_info.value.retryable
    assert host.swap_calls == 2  # every later attempt failed to re-read, so none swapped again


def test_fence_release_makes_one_attempt() -> None:
    host = FenceHost()
    fence = _acquired(host)
    host.lost_swaps = 100
    fence.release()
    assert host.swap_calls == 2


# --- executor lifecycle -----------------------------------------------------


def _plan(port: int = 443) -> ResourcePlan:
    return resource_plan(firewall_resource(port))


class _Driver:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.converged: set[str] = set()

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        if action.expected_hash not in self.converged:
            return ResourceObservation(exists=False)
        return ResourceObservation(exists=True, observed_hash=action.expected_hash, remote_id="fw")

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        if self.fail:
            raise RuntimeError("ufw broke")
        self.converged.add(action.expected_hash)
        return ResourceApplyReceipt(remote_id="fw")


class _RecordingFence:
    def __init__(self, refuse: bool = False) -> None:
        self.calls: list[Any] = []
        self.refuse = refuse

    def acquire(self, local: LocalFenceView) -> str:
        self.calls.append(("acquire", local))
        if self.refuse:
            raise FleetFenceError("Another machine changed this fleet.")
        return local.fleet_id or _FLEET

    def ensure(self, local: LocalFenceView) -> None:
        return None

    def check(self, local: LocalFenceView) -> str:
        return ""

    def renew(self) -> None:
        self.calls.append("renew")

    def commit(self, generation: int, plan_hash: str) -> None:
        self.calls.append(("commit", generation))

    def release(self) -> None:
        self.calls.append("release")


def _apply(cluster: ClusterConfig, fence: _RecordingFence, driver: _Driver | None = None) -> Any:
    return execute_resource_plan(
        _plan(),
        cluster,
        {"firewall_rule": driver or _Driver()},
        persist=lambda _state: None,
        clock=lambda: _NOW,
        fence=fence,
    )


def test_executor_takes_the_fence_first_commits_and_releases() -> None:
    cluster, fence = ClusterConfig(), _RecordingFence()
    result = _apply(cluster, fence)
    assert result.all_succeeded
    assert fence.calls == [("acquire", _local(fleet_id="", fresh=True)), "renew", ("commit", 1), "release"]
    assert cluster.fleet_id == _FLEET


def test_refused_fence_changes_nothing() -> None:
    cluster, driver = ClusterConfig(), _Driver()
    with pytest.raises(FleetFenceError):
        _apply(cluster, _RecordingFence(refuse=True), driver)
    assert driver.converged == set()
    assert cluster.pending_generation == 0 and cluster.fleet_id == ""


def test_failed_apply_releases_without_committing() -> None:
    cluster, fence = ClusterConfig(), _RecordingFence()
    result = _apply(cluster, fence, _Driver(fail=True))
    assert not result.all_succeeded
    assert fence.calls[-1] == "release"
    assert not any(isinstance(call, tuple) and call[0] == "commit" for call in fence.calls)


def test_fleet_id_round_trips_through_cluster_yml(tmp_home: Any) -> None:
    cluster = ClusterConfig()
    cluster.fleet_id = _FLEET
    cluster.save()
    assert ClusterConfig.load().fleet_id == _FLEET
