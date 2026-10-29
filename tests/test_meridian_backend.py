"""Meridian control-service backend: the skeleton plan shape, its refusals, and the hidden deploy switch."""

from __future__ import annotations

import pytest

from meridian.compiler import compile_topology
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import CertificatePayload, NodeWorkloadPayload
from meridian.core.deploy import DeployRequest
from meridian.core.errors import LocalStateError
from meridian.core.topology import ExitIntent, TransparentRelayIntent
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
    endpoints = sorted(item.logical_id for item in plan.resources if item.payload.kind == "client_endpoint")
    assert endpoints == [
        "endpoint:main:main-hysteria2:direct",
        "endpoint:main:main-reality:direct",
        "endpoint:main:main-xhttp:direct",
    ]


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


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"forward_client_ips": True}, "client IP forwarding"),
        (
            {
                "transparent_relays": [
                    TransparentRelayIntent(
                        id="r1", hop_server_refs=["srv-r"], exit_ref="main", protocol_path_ref="main-reality"
                    )
                ]
            },
            "transparent relays",
        ),
    ],
    ids=["client-ips", "relay"],
)
def test_meridian_backend_refuses_what_the_skeleton_cannot_run(changes: dict[str, object], named: str) -> None:
    with pytest.raises(TopologyCompileError, match=named):
        compile_topology(_intent(**changes))


def test_meridian_backend_refuses_an_exit_off_the_control_server() -> None:
    intent = _intent()
    moved = ExitIntent.model_validate({**intent.exits[0].model_dump(), "server_ref": "srv-b"})

    with pytest.raises(TopologyCompileError, match="one on the control server"):
        compile_topology(intent.model_copy(update={"exits": [moved]}))


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
