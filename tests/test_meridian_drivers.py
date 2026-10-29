"""Meridian control-service drivers against a fake admin API and a mocked server."""

from __future__ import annotations

import base64
import hashlib
import json
from types import TracebackType
from typing import Any

import pytest

from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ResourcePlan
from meridian.control.models import ClientEndpoint, MintedToken, NodeSpec, NodeStatus
from meridian.control_artifacts import AgentBinary
from meridian.core.agent_types import NodeStateRef, canonical_sha256
from meridian.host_runtime import AGENT_BINARY, AGENT_BOOTSTRAP, CONTROL_SERVICE_PUBLIC_KEY
from meridian.reconciler.meridian_drivers import MeridianDriverContext, build_meridian_drivers
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    build_resource_actions,
    observation_converges,
)
from meridian.reconciler.workloads import LocalRealityKeyFactory, WorkloadStateManager
from meridian.setup.single_server import single_server_intent
from tests.support.mock_connection import MockConnection

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
SERVICE_KEY = base64.b64encode(bytes(range(32))).decode()
BINARY = AgentBinary(data=b"\x7fELF-agent", sha256=hashlib.sha256(b"\x7fELF-agent").hexdigest())


class FakeControl:
    """The admin API calls the drivers make, with scripted node reports."""

    def __init__(self) -> None:
        self.specs: dict[str, NodeSpec] = {}
        self.endpoints: dict[str, ClientEndpoint] = {}
        self.users: set[str] = set()
        self.tokens: list[str] = []
        # Each node() call pops the next scripted status; the last one repeats.
        self.reports: list[dict[str, Any]] = []

    def __enter__(self) -> FakeControl:
        return self

    def __exit__(self, *args: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def node(self, node_id: str) -> NodeStatus | None:
        if node_id not in self.specs and not self.reports:
            return None
        report = self.reports.pop(0) if len(self.reports) > 1 else (self.reports[0] if self.reports else {})
        return self._status(node_id, **report)

    def put_node(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        self.specs[node_id] = spec
        return self._status(node_id)

    def mint_token(self, node_id: str) -> MintedToken:
        self.tokens.append(f"node-token-{len(self.tokens):032d}")
        return MintedToken(node_id=node_id, node_token=self.tokens[-1])

    def get_endpoint(self, endpoint_id: str) -> ClientEndpoint | None:
        return self.endpoints.get(endpoint_id)

    def put_endpoint(self, endpoint_id: str, endpoint: ClientEndpoint) -> ClientEndpoint:
        self.endpoints[endpoint_id] = endpoint
        return endpoint

    def _status(self, node_id: str, *, applied: bool = False, **fields: Any) -> NodeStatus:
        spec = self.specs.get(node_id)
        sha = "b" * 64
        return NodeStatus(
            node_id=node_id,
            fleet_epoch=1,
            revision=1,
            sha256=sha,
            spec_sha256=canonical_sha256(spec.model_dump(mode="json")) if spec else None,
            applied=NodeStateRef(fleet_epoch=1, revision=1, sha256=sha) if applied else None,
            last_seen_at=fields.pop("last_seen_at", None),
            agent_version=None,
            **fields,
        )


@pytest.fixture
def plan() -> ResourcePlan:
    return compile_topology(single_server_intent(None, server_ref="srv-a", address="198.51.100.10", control="meridian"))


def _setup(plan: ResourcePlan, conn: MockConnection | None = None):
    cluster = ClusterConfig()
    cluster.fleet_id = FLEET_ID
    cluster.panel.url = "https://198.51.100.10/secret"
    control = FakeControl()
    drivers = build_meridian_drivers(
        MeridianDriverContext(
            plan=plan,
            cluster=cluster,
            client=lambda: control,  # type: ignore[arg-type,return-value]
            workloads=WorkloadStateManager(cluster, persist=lambda _c: None, key_factory=LocalRealityKeyFactory()),
            connection_for=lambda _ref: conn or MockConnection(),
            server_addresses={"srv-a": "198.51.100.10"},
            agent_binary=lambda _arch: BINARY,
            control_ref="srv-a",
            sleep=lambda _s: None,
        )
    )
    return drivers, control, cluster


def _action(plan: ResourcePlan, logical_id: str) -> ResourceAction:
    return next(action for action in build_resource_actions(plan, 1) if action.resource.logical_id == logical_id)


def test_workload_observation_never_allocates_reality_keys(plan: ResourcePlan) -> None:
    drivers, _control, cluster = _setup(plan)

    observation = drivers["node_workload"].observe(_action(plan, "workload:main"), None)

    assert not observation.exists
    assert cluster.workloads == []


def test_workload_converges_once_the_agent_applies_the_current_revision(plan: ResourcePlan) -> None:
    drivers, control, cluster = _setup(plan)
    action = _action(plan, "workload:main")
    control.reports = [{}, {"applied": True}]

    drivers["node_workload"].apply(action, None)

    assert cluster.workloads[0].reality_keys
    assert observation_converges(action, drivers["node_workload"].observe(action, None))


def test_workload_refuses_a_first_document_the_agent_failed(plan: ResourcePlan) -> None:
    drivers, control, _cluster = _setup(plan)
    control.reports = [{"failed": ["core-config"], "errors": ["xray rejected the config"]}]

    with pytest.raises(ResourceReconcileError, match="xray rejected the config"):
        drivers["node_workload"].apply(_action(plan, "workload:main"), None)


def test_reality_endpoint_publishes_the_workload_public_identity(plan: ResourcePlan) -> None:
    drivers, control, cluster = _setup(plan)
    control.reports = [{"applied": True}]
    drivers["node_workload"].apply(_action(plan, "workload:main"), None)
    action = _action(plan, "endpoint:main:main-reality:direct")

    drivers["client_endpoint"].apply(action, None)

    keys = next(iter(cluster.workloads[0].reality_keys.values()))
    endpoint = control.endpoints["main:main-reality:direct"]
    assert (endpoint.reality_public_key, endpoint.reality_short_id) == (keys.public_key, keys.short_id)
    assert endpoint.address == "198.51.100.10"
    assert observation_converges(action, drivers["client_endpoint"].observe(action, None))


def test_agent_bootstrap_pins_the_service_key_and_a_fresh_token(plan: ResourcePlan) -> None:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    conn.when(f"cat {CONTROL_SERVICE_PUBLIC_KEY}", stdout=f"{SERVICE_KEY}\n")
    drivers, control, _cluster = _setup(plan, conn)
    control.reports = [{"last_seen_at": "2999-01-01T00:00:00Z"}]

    drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)

    bootstrap = json.loads(conn.write_map[AGENT_BOOTSTRAP])
    assert bootstrap["service_public_key"] == SERVICE_KEY
    assert bootstrap["node_token"] == control.tokens[-1]
    assert bootstrap["service_url"] == "https://198.51.100.10/secret"
    assert conn.write_map[AGENT_BINARY] == BINARY.data
    write = next(kwargs for path, _data, kwargs in conn.writes if path == AGENT_BOOTSTRAP)
    assert write["mode"] == "600"


def test_agent_refuses_a_malformed_service_key(plan: ResourcePlan) -> None:
    conn = MockConnection()
    conn.when("uname -m", stdout="x86_64\n")
    conn.when(f"cat {CONTROL_SERVICE_PUBLIC_KEY}", stdout=base64.b64encode(b"short").decode())
    drivers, control, _cluster = _setup(plan, conn)

    with pytest.raises(ResourceReconcileError, match="malformed"):
        drivers["agent_runtime"].apply(_action(plan, "agent:main"), None)
    assert control.tokens == []
