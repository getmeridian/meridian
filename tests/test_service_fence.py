"""A control-service fleet's apply fence: fence.json while the service is set up, then the service, with revisions."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.enrollment import token_sha256
from meridian.control.models import ApplyFenceRecord
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.reconciler.fence import FleetFenceError, LocalFenceView
from meridian.reconciler.resource_executor import execute_resource_plan
from meridian.service_fence import ControlServiceFence, RevisionDraft
from meridian.state_fence import FENCE_FILE, HANDED_OVER_UNTIL, SERVICE_HOLDER, SSHApplyFence
from tests.support.fence_host import FenceHost
from tests.support.topology import firewall_resource, resource_plan, single_exit_intent

_NOW = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
_FLEET = "11111111-1111-4111-8111-111111111111"
_TOKEN = "admin-token-for-tests-only-0123456789"
_OLD, _NEW = "a" * 64, "c" * 64
_CLI = "4.0.0"


@dataclass
class Service:
    url: str
    service: ControlService
    swaps_lost: int = 0  # swaps that land but whose reply is lost
    approval_lost: bool = False  # the first approval lands, its reply and every retry are lost

    def client(self, timeout: float = 20.0) -> ControlClient:
        return _LossyClient(self, self.url, _TOKEN, timeout=timeout)


class _LossyClient(ControlClient):
    def __init__(self, owner: Service, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._owner = owner

    def swap_fence(self, expected_version: int, record: ApplyFenceRecord) -> Any:
        answer = super().swap_fence(expected_version, record)
        if self._owner.swaps_lost:
            self._owner.swaps_lost -= 1
            raise ControlServiceError("Could not reach the control service: ReadTimeout", retryable=True)
        return answer

    def approve_revision(self, number: int) -> Any:
        if not self._owner.approval_lost:
            return super().approve_revision(number)
        if self._owner.service.revisions.get(number).status == "proposed":
            super().approve_revision(number)
        raise ControlServiceError("Could not reach the control service: ReadTimeout", retryable=True)


@pytest.fixture
def service(tmp_path: Path) -> Iterator[Service]:
    control = ControlService(tmp_path / "control.db", fleet_id=_FLEET, version=_CLI)
    server = ControlServer(("127.0.0.1", 0), control, token_sha256(_TOKEN))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield Service(f"http://127.0.0.1:{server.server_address[1]}", control)
    server.shutdown()
    server.server_close()


def _dead_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _cluster(url: str = "") -> ClusterConfig:
    cluster = ClusterConfig()
    cluster.panel.url = url
    cluster.panel.api_token = _TOKEN if url else ""
    return cluster


def _draft() -> RevisionDraft:
    return RevisionDraft.from_intent(single_exit_intent(), plan_hash=_NEW, review_hash="d" * 64)


def _fence(host: FenceHost, cluster: ClusterConfig, service: Service | None = None, **kwargs: Any) -> Any:
    holder = kwargs.pop("holder", "laptop:1")
    factory = service.client if service is not None else (lambda timeout: ControlClient(cluster.panel.url, _TOKEN))
    return ControlServiceFence(
        host,  # type: ignore[arg-type]
        cluster,
        kwargs.pop("draft", _draft()),
        holder=holder,
        clock=lambda: _NOW,
        client_factory=factory,
        cli_version=kwargs.pop("cli_version", _CLI),
        **kwargs,
    )


def _local(generation: int = 7, plan_hash: str = _OLD, fresh: bool = False) -> LocalFenceView:
    return LocalFenceView(fleet_id=_FLEET, active_generation=generation, active_plan_hash=plan_hash, fresh=fresh)


def _seed_fence_json(host: FenceHost, generation: int = 7, plan_hash: str = _OLD) -> None:
    host.files[FENCE_FILE] = json.dumps({"fleet_id": _FLEET, "generation": generation, "plan_hash": plan_hash})


def _revisions(service: Service) -> list[tuple[int, str]]:
    return [(item.number, item.status) for item in service.service.revisions.list_revisions().revisions]


# --- bootstrap: the first apply creates the service -----------------------------------


def test_the_first_apply_fences_with_fence_json_and_records_its_revision_once_the_service_is_up(
    service: Service,
) -> None:
    host, cluster = FenceHost(), _cluster()
    fence = _fence(host, cluster, service)

    fleet_id = fence.acquire(LocalFenceView(fleet_id="", active_generation=0, active_plan_hash="", fresh=True))
    assert host.record()["lease_holder"] == "laptop:1"
    # The control bootstrap saves how to reach the service it installed.
    cluster.panel.url, cluster.panel.api_token = service.url, _TOKEN
    fence.commit(1, _NEW)
    fence.release()

    assert host.record() == {
        "fleet_id": fleet_id,
        "generation": 1,
        "plan_hash": _NEW,
        "lease_holder": "",
        "lease_expires_at": "",
    }
    assert _revisions(service) == [(1, "applied")]
    assert service.service.revisions.fence().version == 0  # adopted by the next apply


def test_a_retried_bootstrap_whose_service_never_came_up_keeps_using_fence_json() -> None:
    host, cluster = FenceHost(), _cluster(_dead_url())
    _seed_fence_json(host, generation=0, plan_hash="")
    fence = _fence(host, cluster)

    fence.acquire(_local(generation=0, plan_hash=""))
    fence.release()

    assert host.record()["lease_holder"] == "" and host.swaps == 2


# --- migration: an existing fleet with only fence.json ---------------------------------


def test_the_first_apply_after_the_upgrade_moves_fence_json_into_the_service(service: Service) -> None:
    host, cluster = FenceHost(), _cluster(service.url)
    _seed_fence_json(host)
    fence = _fence(host, cluster, service)

    assert fence.acquire(_local()) == _FLEET
    marked = host.record()
    assert (marked["generation"], marked["plan_hash"]) == (7, _OLD)
    assert (marked["lease_holder"], marked["lease_expires_at"]) == (SERVICE_HOLDER, HANDED_OVER_UNTIL)
    held = service.service.revisions.fence()
    assert held.record is not None and (held.record.generation, held.record.lease_holder) == (7, "laptop:1")
    assert _revisions(service) == [(1, "approved")]

    fence.commit(8, _NEW)
    fence.release()

    committed = service.service.revisions.fence().record
    assert committed is not None
    assert (committed.generation, committed.plan_hash, committed.revision, committed.lease_holder) == (
        8,
        _NEW,
        1,
        "",
    )
    assert _revisions(service) == [(1, "applied")]
    assert host.record() == marked  # fence.json is never written again


def test_a_meridian_that_predates_the_move_is_refused_by_the_handed_over_file(service: Service) -> None:
    host = FenceHost()
    _seed_fence_json(host)
    fence = _fence(host, _cluster(service.url), service)
    fence.acquire(_local())
    fence.commit(8, _NEW)

    old = SSHApplyFence(host, holder="desktop:2", clock=lambda: _NOW)  # type: ignore[arg-type]
    with pytest.raises(FleetFenceError, match=f"Another apply is running from {SERVICE_HOLDER}"):
        old.acquire(_local(generation=8, plan_hash=_NEW))


def test_a_move_interrupted_after_marking_fence_json_is_adopted_from_the_marked_state(service: Service) -> None:
    host, cluster = FenceHost(), _cluster(service.url)
    host.files[FENCE_FILE] = json.dumps(
        {
            "fleet_id": _FLEET,
            "generation": 7,
            "plan_hash": _OLD,
            "lease_holder": SERVICE_HOLDER,
            "lease_expires_at": HANDED_OVER_UNTIL,
        }
    )
    fence = _fence(host, cluster, service)
    fence.acquire(_local())
    fence.commit(8, _NEW)

    record = service.service.revisions.fence().record
    assert record is not None and record.generation == 8
    assert host.swaps == 0


# --- steady state ----------------------------------------------------------------------


def _moved(host: FenceHost, service: Service) -> None:
    _seed_fence_json(host)
    fence = _fence(host, _cluster(service.url), service)
    fence.acquire(_local())
    fence.commit(8, _NEW)


def test_steady_state_applies_never_read_fence_json(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    host.files[FENCE_FILE] = "{unreadable"  # proves nothing reads it

    fence = _fence(host, _cluster(service.url), service)
    fence.ensure(_local(generation=8, plan_hash=_NEW))
    fence.acquire(_local(generation=8, plan_hash=_NEW))
    fence.commit(9, "e" * 64)

    assert _revisions(service) == [(2, "applied"), (1, "applied")]
    record = service.service.revisions.fence().record
    assert record is not None and (record.generation, record.revision) == (9, 2)


def test_a_stale_copy_is_refused_by_the_service_and_records_nothing(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    stale = _fence(host, _cluster(service.url), service)

    assert "Another machine changed this fleet" in stale.check(_local())
    with pytest.raises(FleetFenceError, match="Another machine changed this fleet"):
        stale.acquire(_local())
    assert _revisions(service) == [(1, "applied")]


def test_a_second_machine_is_refused_while_the_first_holds_the_lease(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    _fence(host, _cluster(service.url), service).acquire(_local(generation=8, plan_hash=_NEW))

    other = _fence(host, _cluster(service.url), service, holder="desktop:2")
    with pytest.raises(FleetFenceError, match="Another apply is running from laptop"):
        other.acquire(_local(generation=8, plan_hash=_NEW))


def test_a_failed_apply_marks_its_revision_failed_and_frees_the_lease(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    fence = _fence(host, _cluster(service.url), service)
    fence.acquire(_local(generation=8, plan_hash=_NEW))
    fence.release()

    assert _revisions(service) == [(2, "failed"), (1, "applied")]
    record = service.service.revisions.fence().record
    assert record is not None and (record.lease_holder, record.generation, record.revision) == ("", 8, 1)


def test_an_approval_whose_reply_was_lost_is_marked_failed_when_the_lease_is_freed(service: Service) -> None:
    """Left approved, the revision would stay in flight in the history forever."""
    host = FenceHost()
    _moved(host, service)
    service.approval_lost = True

    with pytest.raises(FleetFenceError, match="ReadTimeout"):
        _fence(host, _cluster(service.url), service).acquire(_local(generation=8, plan_hash=_NEW))

    assert _revisions(service) == [(2, "failed"), (1, "applied")]
    record = service.service.revisions.fence().record
    assert record is not None and record.lease_holder == ""


def test_an_unreachable_service_is_refused_after_retries_never_bypassed_through_fence_json(
    service: Service,
) -> None:
    host = FenceHost()
    _moved(host, service)
    stalls: list[int] = []
    cluster = _cluster(_dead_url())
    fence = _fence(host, cluster, on_stall=lambda attempt, _delay, _exc: stalls.append(attempt))

    with pytest.raises(FleetFenceError, match="does not answer") as refused:
        fence.acquire(_local(generation=8, plan_hash=_NEW))

    assert refused.value.category == "system" and refused.value.retryable
    assert stalls == [1, 2, 3, 4]
    assert host.record()["lease_holder"] == SERVICE_HOLDER


def test_a_commit_whose_reply_was_lost_is_re_read_not_repeated(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    fence = _fence(host, _cluster(service.url), service)
    fence.acquire(_local(generation=8, plan_hash=_NEW))
    service.swaps_lost = 1
    fence.commit(9, "e" * 64)

    state = service.service.revisions.fence()
    assert state.record is not None and state.record.generation == 9
    assert state.version == 4  # move, commit, lease, commit: the lost reply's swap was not repeated


def test_the_executor_records_a_failed_apply_through_the_service(service: Service) -> None:
    host = FenceHost()
    _moved(host, service)
    cluster = _cluster(service.url)
    cluster.fleet_id = _FLEET

    class _Broken:
        def observe(self, action: Any, binding: Any) -> Any:
            from meridian.reconciler.resources import ResourceObservation

            return ResourceObservation(exists=False)

        def apply(self, action: Any, binding: Any) -> Any:
            raise RuntimeError("ufw broke")

    # A copy at generation 8 with this plan hash.
    cluster.active_generation, cluster.active_plan_hash = 8, _NEW
    result = execute_resource_plan(
        resource_plan(firewall_resource(443)),
        cluster,
        {"firewall_rule": _Broken()},
        persist=lambda _state: None,
        clock=lambda: _NOW,
        fence=_fence(host, cluster, service),
    )

    assert not result.all_succeeded
    assert _revisions(service) == [(2, "failed"), (1, "applied")]


def test_a_draft_carries_the_secret_free_intent_and_the_reviewed_hashes() -> None:
    draft = _draft()
    assert draft.intent == single_exit_intent().model_dump(mode="json")
    assert draft.proposal().plan_hash == _NEW


@pytest.mark.parametrize(("backend", "kind"), [("remnawave", SSHApplyFence), ("meridian", ControlServiceFence)])
def test_setup_runtime_fences_by_the_reviewed_control_backend(tmp_path: Path, backend: str, kind: type) -> None:
    from meridian.core.topology import ControlPlaneIntent
    from meridian.servers import ServerRegistry
    from meridian.setup.runtime import SetupReview, SetupRuntime

    intent = single_exit_intent(control=ControlPlaneIntent(server_ref="srv-control", backend=backend))
    review = SetupReview(intent=intent, plan=resource_plan(firewall_resource(443)))
    runtime = SetupRuntime(ServerRegistry(tmp_path / "servers.json"))

    fence = runtime._default_fence(FenceHost(), _cluster(), review)  # type: ignore[arg-type]

    assert type(fence) is kind


def test_a_copy_without_the_services_credentials_is_refused_once_the_service_keeps_the_fence(
    service: Service,
) -> None:
    host = FenceHost()
    _moved(host, service)

    with pytest.raises(FleetFenceError, match="cannot reach it") as refused:
        _fence(host, _cluster()).acquire(_local(generation=0, plan_hash="", fresh=True))
    assert refused.value.category == "user"


def test_an_older_service_without_the_fence_route_keeps_fence_json_until_it_is_upgraded(service: Service) -> None:
    from meridian.control.client import ControlServiceNotFound

    class _OlderService(ControlClient):
        def fence(self) -> Any:
            raise ControlServiceNotFound("Not found.", category="user")

    host, cluster = FenceHost(), _cluster(service.url)
    _seed_fence_json(host)
    fence = _fence(host, cluster)
    fence._client_factory = lambda timeout: _OlderService(service.url, _TOKEN)

    fence.acquire(_local())
    fence.commit(8, _NEW)

    assert host.record()["generation"] == 8 and host.record()["lease_holder"] == ""
    # The apply reinstalled the service; it records the revision, and the next apply adopts fence.json.
    assert _revisions(service) == [(1, "applied")]
    assert service.service.revisions.fence().version == 0


# --- version skew: the service never runs older code than the copy that applies ------------


@pytest.mark.parametrize(
    ("cli", "refused"),
    [("4.0.0", False), ("4.0.1", False), ("4.0.0b8", True), ("3.9.0", True), ("not-a-version", False)],
    ids=["same", "service-older", "cli-beta-behind", "cli-major-behind", "unparsable"],
)
def test_a_service_newer_than_this_meridian_refuses_the_apply_before_the_lease(
    service: Service, cli: str, refused: bool
) -> None:
    host, cluster = FenceHost(), _cluster(service.url)
    fence = _fence(host, cluster, service, cli_version=cli)

    if not refused:
        fence.acquire(_local())
        fence.release()
        return
    with pytest.raises(FleetFenceError) as raised:
        fence.acquire(_local())
    assert raised.value.category == "user"
    assert "meridian update" in raised.value.hint
    assert service.service.revisions.fence().record is None, "nothing was taken"
    assert _revisions(service) == []
    # Plan shows the same refusal as a warning instead of failing.
    assert "newer than this Meridian" in _fence(host, cluster, service, cli_version=cli).check(_local())
