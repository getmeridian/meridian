"""Meridian control-service backend: the skeleton plan shape, its refusals, and the hidden deploy switch."""

from __future__ import annotations

import pytest

from meridian.compiler import compile_topology
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import CertificatePayload, NodeWorkloadPayload
from meridian.core.deploy import DeployRequest
from meridian.core.errors import LocalStateError
from meridian.core.topology import ControlPlaneIntent, ExitIntent, TransparentRelayIntent
from meridian.setup.default_paths import default_exit_paths
from meridian.setup.single_server import request_intent, single_server_intent

ADDRESS = "198.51.100.10"


def _intent(**changes: object):
    intent = single_server_intent(None, server_ref="srv-a", address=ADDRESS, control="meridian")
    return intent.model_copy(update=changes) if changes else intent


def test_meridian_plan_replaces_every_panel_kind() -> None:
    plan = compile_topology(_intent())
    kinds = {item.payload.kind for item in plan.resources}

    assert {"control_service", "agent_runtime", "node_workload", "control_user", "service_settings"} <= kinds
    assert not kinds & {"control_plane_runtime", "profile", "inbound", "node", "host", "access_user", "squad"}
    endpoints = {
        item.logical_id: item.payload.fingerprint_copy  # type: ignore[union-attr]
        for item in plan.resources
        if item.payload.kind == "client_endpoint"
    }
    assert endpoints == {
        "endpoint:main:main-hysteria2:direct": False,
        "endpoint:main:main-reality:direct": False,
        "endpoint:main:main-reality:direct:firefox": True,
        "endpoint:main:main-xhttp:direct": False,
        "endpoint:main:main-xhttp:direct:firefox": True,
    }


def test_meridian_plan_keeps_certificates_independent_of_the_workload() -> None:
    """The workload needs the certificate; the reverse would deadlock the first apply."""
    plan = compile_topology(_intent())
    by_id = {item.logical_id: item for item in plan.resources}
    certificate = next(item for item in plan.resources if isinstance(item.payload, CertificatePayload))
    workload = next(item for item in plan.resources if isinstance(item.payload, NodeWorkloadPayload))

    assert workload.logical_id not in certificate.dependencies
    assert certificate.logical_id in by_id[workload.logical_id].dependencies


def test_meridian_baseline_skips_docker() -> None:
    plan = compile_topology(_intent())
    baseline = next(item for item in plan.resources if item.payload.kind == "server_baseline")

    assert baseline.payload.install_docker is False  # type: ignore[union-attr]


def _relay() -> TransparentRelayIntent:
    return TransparentRelayIntent(id="r1", hop_server_refs=["srv-r"], exit_ref="main", protocol_path_ref="main-reality")


def _second_exit() -> ExitIntent:
    return ExitIntent(id="de", server_ref="srv-b", paths=default_exit_paths("de", address="198.51.100.20"))


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"forward_client_ips": True}, "client IP forwarding"),
        (
            {
                "control": ControlPlaneIntent(
                    server_ref="srv-a", public_hostname="panel.example.com", backend="meridian"
                )
            },
            "control-plane domain",
        ),
    ],
    ids=["client-ips", "control-domain"],
)
def test_meridian_backend_refuses_what_the_service_cannot_run(changes: dict[str, object], named: str) -> None:
    with pytest.raises(TopologyCompileError, match=named):
        compile_topology(_intent(**changes))


def test_meridian_backend_refuses_a_control_server_without_an_exit() -> None:
    intent = _intent()
    moved = intent.exits[0].model_copy(update={"server_ref": "srv-b"})

    with pytest.raises(TopologyCompileError, match="control server without an exit"):
        compile_topology(intent.model_copy(update={"exits": [moved]}))


def test_every_exit_gets_its_own_agent_and_workload() -> None:
    plan = compile_topology(_intent(exits=[*_intent().exits, _second_exit()]))
    placed = {
        (item.payload.kind, item.payload.server_ref)
        for item in plan.resources
        if item.payload.kind in {"agent_runtime", "node_workload"}
    }  # type: ignore[union-attr]

    assert placed == {
        ("agent_runtime", "srv-a"),
        ("node_workload", "srv-a"),
        ("agent_runtime", "srv-b"),
        ("node_workload", "srv-b"),
    }


def test_relay_endpoint_takes_its_keys_from_the_exit_and_its_address_from_the_relay() -> None:
    plan = compile_topology(_intent(transparent_relays=[_relay()]))
    relay = next(item for item in plan.resources if item.logical_id == "endpoint:r1:main-reality")

    assert relay.payload.workload_ref == "main"  # type: ignore[union-attr]
    assert relay.payload.address_server_ref == "srv-r"  # type: ignore[union-attr]
    assert "workload:main" in relay.dependencies
    assert any(item.payload.kind == "realm_hop" for item in plan.resources)


def test_default_backend_stays_out_of_saved_intent() -> None:
    """Existing cluster.yml files and contracts never see the hidden switch."""
    remnawave = single_server_intent(None, server_ref="srv-a")

    assert "backend" not in remnawave.model_dump(mode="json")["control"]
    assert _intent().model_dump(mode="json")["control"]["backend"] == "meridian"
    assert "control" not in DeployRequest().model_dump()


def test_redeploy_cannot_switch_the_control_backend() -> None:
    existing = single_server_intent(None, server_ref="srv-a", address=ADDRESS)

    with pytest.raises(LocalStateError, match="cannot change"):
        request_intent(existing, DeployRequest(control="meridian"), server_ref="srv-a", address=ADDRESS)


def test_redeploy_keeps_the_meridian_backend_without_the_switch() -> None:
    intent = request_intent(_intent(), DeployRequest(), server_ref="srv-a", address=ADDRESS)

    assert intent.control.backend == "meridian"


@pytest.mark.parametrize("target", [ADDRESS, "198.51.100.20", "198.51.100.30"], ids=["control", "exit", "relay"])
def test_verification_sees_a_control_service_fleet_like_a_panel_fleet(tmp_path, target: str) -> None:
    """`meridian test` dropped route checks on a control-service fleet until it read client endpoints."""
    from meridian.cluster import ClusterConfig
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.verification import resolve_deployment_verification_context

    registry = ServerRegistry(tmp_path / "servers.json")
    for ref, host in (("srv-a", ADDRESS), ("srv-b", "198.51.100.20"), ("srv-r", "198.51.100.30")):
        registry.add(ServerEntry(host=host, user="root", id=ref))
    meridian = _intent(exits=[*_intent().exits, _second_exit()], transparent_relays=[_relay()])
    panel = meridian.model_copy(update={"control": meridian.control.model_copy(update={"backend": "remnawave"})})

    contexts = []
    for intent in (meridian, panel):
        cluster = ClusterConfig()
        cluster.topology_intent = intent
        contexts.append(resolve_deployment_verification_context(registry, target, cluster=cluster)[1])

    assert contexts[0] == contexts[1]
    assert contexts[0].https_routes
