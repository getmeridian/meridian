"""Truthful runtime driver observations for V4 control and probes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    COMPILER_VERSION,
    AccessUserPayload,
    ControlPlaneRuntimePayload,
    HostPayload,
    NodeBindingPayload,
    NodeRuntimePayload,
    ProbePayload,
    ResourcePlan,
    RoutingGatewayPayload,
    compute_plan_hash,
    make_resource,
)
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    UnknownResourceOutcome,
    assert_complete_driver_registry,
    build_resource_actions,
    observation_converges,
)
from meridian.reconciler.runtime_drivers import (
    ControlPlaneDriverContext,
    ControlPlaneRuntimeDriver,
    ProbeDriver,
    ProbeDriverContext,
    xray_subscription_is_valid,
)


def _plan(*resources) -> ResourcePlan:
    intent_hash = "1" * 64
    return ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=list(resources),
        ),
        resources=list(resources),
    )


def test_control_runtime_bootstraps_then_requires_panel_readiness() -> None:
    resource = make_resource(
        "control:runtime",
        ControlPlaneRuntimePayload(server_ref="srv-control"),
    )
    plan = _plan(resource)
    action = build_resource_actions(plan, 1)[0]
    ready = False

    def bootstrap(_action) -> None:
        nonlocal ready
        ready = True

    driver = ControlPlaneRuntimeDriver(
        ControlPlaneDriverContext(
            ready=lambda _action: ready,
            bootstrap=bootstrap,
        )
    )

    assert not observation_converges(action, driver.observe(action, None))
    driver.apply(action, None)
    assert observation_converges(action, driver.observe(action, None))


def test_control_runtime_preserves_unknown_outcome_on_bootstrap_timeout() -> None:
    resource = make_resource(
        "control:runtime",
        ControlPlaneRuntimePayload(server_ref="srv-control"),
    )
    action = build_resource_actions(_plan(resource), 1)[0]

    def timeout(_action) -> None:
        raise TimeoutError("bootstrap timed out")

    driver = ControlPlaneRuntimeDriver(
        ControlPlaneDriverContext(
            ready=lambda _action: False,
            bootstrap=timeout,
        )
    )

    with pytest.raises(UnknownResourceOutcome, match="observation is required"):
        driver.apply(action, None)


def test_production_driver_registry_requires_every_finite_kind() -> None:
    try:
        assert_complete_driver_registry({})
    except ResourceReconcileError as exc:
        assert "control_plane_runtime" in str(exc)
        assert "probe" in str(exc)
    else:
        raise AssertionError("an incomplete driver registry was accepted")


def _listener_probe_driver(
    *,
    returncode: int,
    stdout: str,
) -> tuple[ResourceAction, ProbeDriver, MagicMock]:
    host = make_resource(
        "host:exit-a:reality:direct",
        HostPayload(
            owner_ref="exit-a",
            remark="M4 exit-a",
            node_ref="node:exit-a",
            inbound_ref="inbound:exit-a:reality",
            address_server_ref="srv-exit",
            public_port=443,
            protocol="reality",
        ),
    )
    probe = make_resource(
        "probe:host",
        ProbePayload(
            probe="listener",
            target_ref=host.logical_id,
            protocol="reality",
        ),
        dependencies=[host.logical_id],
    )
    plan = _plan(host, probe)
    action = build_resource_actions(plan, 1)[1]
    connection = MagicMock()
    connection.run.return_value = SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
    )
    driver = ProbeDriver(
        ProbeDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )
    return action, driver, connection


def test_listener_probe_reads_the_target_server_socket_table() -> None:
    action, driver, connection = _listener_probe_driver(
        returncode=0,
        stdout="LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n",
    )

    assert observation_converges(action, driver.observe(action, None))
    connection.run.assert_called_once_with("ss -H -lnt 2>/dev/null", timeout=15)


def test_listener_probe_accepts_successful_socket_table_as_absence_evidence() -> None:
    action, driver, _connection = _listener_probe_driver(returncode=0, stdout="")

    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize("returncode", [1, 124, 127, 255])
def test_listener_probe_rejects_unavailable_socket_table(returncode: int) -> None:
    action, driver, _connection = _listener_probe_driver(returncode=returncode, stdout="")

    with pytest.raises(ResourceReconcileError, match="Listener evidence is unavailable"):
        driver.observe(action, None)


def test_subscription_probe_fetches_the_canonical_document() -> None:
    user = make_resource(
        "user:access",
        AccessUserPayload(username="access", squad_ref="squad:access"),
    )
    probe = make_resource(
        "probe:subscription:access",
        ProbePayload(probe="subscription", target_ref=user.logical_id),
        dependencies=[user.logical_id],
    )
    plan = _plan(user, probe)
    action = build_resource_actions(plan, 1)[1]
    panel = MagicMock()
    panel.get_user.return_value = SimpleNamespace(short_uuid="short-access")
    panel.fetch_subscription.return_value = SimpleNamespace(
        url="https://panel.example/sub/short-access",
        content=json.dumps(
            [
                {
                    "inbounds": [
                        {
                            "tag": "SOCKS",
                            "listen": "127.0.0.1",
                            "port": 1080,
                            "protocol": "socks",
                        }
                    ],
                    "outbounds": [{"tag": "MERIDIAN_PROXY_1", "protocol": "vless"}],
                }
            ]
        ),
    )
    driver = ProbeDriver(
        ProbeDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=panel,
            connection_for=MagicMock(),
            server_addresses={},
        )
    )

    assert observation_converges(action, driver.observe(action, None))
    panel.fetch_subscription.assert_called_once_with("short-access", client_type="json")


def test_subscription_probe_rejects_noncanonical_html() -> None:
    user = make_resource(
        "user:access",
        AccessUserPayload(username="access", squad_ref="squad:access"),
    )
    probe = make_resource(
        "probe:subscription:access",
        ProbePayload(probe="subscription", target_ref=user.logical_id),
        dependencies=[user.logical_id],
    )
    plan = _plan(user, probe)
    panel = MagicMock()
    panel.get_user.return_value = SimpleNamespace(short_uuid="short-access")
    panel.fetch_subscription.return_value = SimpleNamespace(
        url="https://panel.example/sub/short-access/json",
        content="<html>stale proxy page</html>",
    )
    driver = ProbeDriver(
        ProbeDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=panel,
            connection_for=MagicMock(),
            server_addresses={},
        )
    )

    action = build_resource_actions(plan, 1)[1]
    assert not observation_converges(action, driver.observe(action, None))


def test_subscription_probe_rejects_multiple_xray_configs() -> None:
    user = make_resource(
        "user:access",
        AccessUserPayload(username="access", squad_ref="squad:access"),
    )
    probe = make_resource(
        "probe:subscription:access",
        ProbePayload(probe="subscription", target_ref=user.logical_id),
        dependencies=[user.logical_id],
    )
    plan = _plan(user, probe)
    config = '{"outbounds": [{"tag": "MERIDIAN_PROXY", "protocol": "vless"}]}'
    panel = MagicMock()
    panel.get_user.return_value = SimpleNamespace(short_uuid="short-access")
    panel.fetch_subscription.return_value = SimpleNamespace(
        url="https://panel.example/sub/short-access/json",
        content=f"[{config},{config}]",
    )
    driver = ProbeDriver(
        ProbeDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=panel,
            connection_for=MagicMock(),
            server_addresses={},
        )
    )

    action = build_resource_actions(plan, 1)[1]
    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize(
    ("outbound", "expected"),
    [
        ({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria", "settings": {"version": 2}}, True),
        ({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria", "settings": {"version": 1}}, False),
        ({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria2", "settings": {"version": 2}}, False),
    ],
)
def test_subscription_probe_requires_remnawave_hysteria_v2_shape(outbound: dict, expected: bool) -> None:
    document = SimpleNamespace(
        url="https://panel.example/sub/short-access/json",
        content=json.dumps(
            [
                {
                    "inbounds": [
                        {
                            "tag": "SOCKS",
                            "listen": "127.0.0.1",
                            "port": 1080,
                            "protocol": "socks",
                        }
                    ],
                    "outbounds": [outbound],
                }
            ]
        ),
    )

    assert xray_subscription_is_valid(document) is expected


def test_gateway_probe_requires_connected_node_on_reviewed_profile() -> None:
    binding = make_resource(
        "binding:gateway-a",
        NodeBindingPayload(
            workload_ref="gateway-a",
            server_ref="srv-gateway",
            name="M4 gateway-a",
            profile_ref="profile:gateway-a",
            inbound_refs=["inbound:gateway-a"],
        ),
    )
    runtime = make_resource(
        "node:gateway-a",
        NodeRuntimePayload(
            workload_ref="gateway-a",
            server_ref="srv-gateway",
            binding_ref=binding.logical_id,
        ),
        dependencies=[binding.logical_id],
    )
    gateway = make_resource(
        "gateway:gateway-a",
        RoutingGatewayPayload(
            gateway_id="gateway-a",
            server_ref="srv-gateway",
            bridge_path_ref="exit-a-reality",
            profile_ref="profile:gateway-a",
            node_ref=runtime.logical_id,
            inbound_ref="inbound:gateway-a",
        ),
        dependencies=[runtime.logical_id],
    )
    probe = make_resource(
        "probe:route:gateway-a",
        ProbePayload(probe="route", target_ref=gateway.logical_id),
        dependencies=[gateway.logical_id],
    )
    plan = _plan(binding, runtime, gateway, probe)
    node_binding = ManagedResourceBinding(
        logical_id=binding.logical_id,
        resource_kind="node_binding",
        generation=1,
        remote_id="node-uuid",
    )
    profile_binding = ManagedResourceBinding(
        logical_id="profile:gateway-a",
        resource_kind="config_profile",
        generation=1,
        remote_id="profile-uuid",
    )
    cluster = ClusterConfig(
        managed_bindings={
            f"{binding.logical_id}@1": node_binding,
            "profile:gateway-a@1": profile_binding,
        }
    )
    panel = MagicMock()
    panel.get_node.return_value = SimpleNamespace(
        is_disabled=False,
        is_connected=True,
        active_config_profile_uuid="profile-uuid",
    )
    action = build_resource_actions(plan, 1)[-1]
    driver = ProbeDriver(
        ProbeDriverContext(
            plan=plan,
            cluster=cluster,
            panel=panel,
            connection_for=MagicMock(),
            server_addresses={"srv-gateway": "198.51.100.20"},
        )
    )

    assert observation_converges(action, driver.observe(action, None))
