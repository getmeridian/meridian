"""Shared builders for topology intents, compiled plans, and panel stand-ins.

Defaults describe the smallest fleet: a control server plus one exit with one
Reality path. Override only what the test is about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import MagicMock

from meridian.compiler import compile_topology
from meridian.compiler.models import (
    COMPILER_VERSION,
    CompiledResource,
    FirewallRulePayload,
    ResourcePlan,
    compute_plan_hash,
    make_resource,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
)

INTENT_HASH = "1" * 64


def reality_path(path_id: str = "reality-a", sni: str = "www.example.com") -> ProtocolPathIntent:
    return ProtocolPathIntent(id=path_id, protocol="reality", reality_sni=sni)


def single_exit_intent(
    *,
    control: str | ControlPlaneIntent = "srv-control",
    exit_id: str = "exit-a",
    exit_ref: str = "srv-exit",
    paths: Sequence[ProtocolPathIntent] | None = None,
    users: Sequence[str] = ("default",),
    exit_fields: Mapping[str, Any] | None = None,
    **intent_fields: Any,
) -> SetupIntent:
    """One exit (``exit_fields`` extend it) that is also the default egress."""
    return SetupIntent(
        control=ControlPlaneIntent(server_ref=control) if isinstance(control, str) else control,
        exits=[
            ExitIntent(
                id=exit_id,
                server_ref=exit_ref,
                paths=list(paths) if paths is not None else [reality_path()],
                **(exit_fields or {}),
            )
        ],
        default_egress_ref=exit_id,
        access=AccessIntent(users=list(users)),
        **intent_fields,
    )


def compile_panel_pass(intent: SetupIntent, **options: Any) -> ResourcePlan:
    """Compile the panel-shaped first pass every plan starts from.

    The Meridian backend rewrites this pass (`compiler/meridian_backend.py`, tested in
    `test_meridian_backend.py`); tests of topology decisions it carries over read it here.
    """
    control = intent.control.model_copy(update={"backend": "remnawave"})
    return compile_topology(intent.model_copy(update={"control": control}), **options)


def resource_plan(*resources: CompiledResource) -> ResourcePlan:
    """A plan over *resources* with a correct plan hash."""
    return ResourcePlan(
        intent_hash=INTENT_HASH,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=INTENT_HASH,
            resources=list(resources),
        ),
        resources=list(resources),
    )


def firewall_resource(port: int = 443, *, server_ref: str = "srv-exit", logical_id: str = "") -> CompiledResource:
    return make_resource(
        logical_id or f"firewall:{server_ref}:tcp:{port}",
        FirewallRulePayload(server_ref=server_ref, transport="tcp", port=port),
    )


def panel_mock(**return_values: Any) -> MagicMock:
    """MeridianPanel stand-in usable as a context manager; kwargs set method return values."""
    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.__exit__.return_value = False
    panel.ping.return_value = True
    for method, value in return_values.items():
        getattr(panel, method).return_value = value
    return panel


GATEWAY_SERVERS = {"srv-a": "198.51.100.10", "srv-b": "198.51.100.20", "srv-g": "198.51.100.40"}


def gateway_fleet_intent() -> SetupIntent:
    """Exits `main` (srv-a, the control server) and `de` (srv-b) behind routing gateway `gw` (srv-g).

    The gateway blocks one domain, sends one out of its own address, one to `de`, and the
    rest to pool `both`; addresses are `GATEWAY_SERVERS`.
    """
    from meridian.setup.default_paths import default_exit_paths
    from meridian.setup.single_server import single_server_intent

    intent = single_server_intent(None, server_ref="srv-a", address=GATEWAY_SERVERS["srv-a"])
    second = ExitIntent(id="de", server_ref="srv-b", paths=default_exit_paths("de", address=GATEWAY_SERVERS["srv-b"]))
    return intent.model_copy(
        update={
            "exits": [*intent.exits, second],
            "routing_gateways": [RoutingGatewayIntent(id="gw", server_ref="srv-g", bridge_path_ref="main-reality")],
            "egress_pools": [EgressPoolIntent(id="both", exit_refs=["main", "de"])],
            "routes": [
                OrderedRouteIntent(
                    id="ads", priority=0, match="domain", match_values=["ads.example.org"], action="block"
                ),
                OrderedRouteIntent(
                    id="local", priority=1, match="domain", match_values=["example.net"], action="direct"
                ),
                OrderedRouteIntent(
                    id="video", priority=2, match="domain", match_values=["video.example.com"], target_ref="de"
                ),
            ],
            "default_egress_ref": "both",
        }
    )
