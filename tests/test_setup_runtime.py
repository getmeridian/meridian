"""Setup runtime wiring from reviewed intent through resumable apply."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler.models import ResourcePlan
from meridian.core.errors import LocalStateError
from meridian.core.setup import SetupDraft
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)
from meridian.reconciler.contract_drivers import ContractResourceDriver
from meridian.reconciler.resources import ResourceDrivers
from meridian.remnawave import MeridianPanel
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.runtime import SetupRuntime
from meridian.ssh import ServerConnection


class _Panel:
    def __init__(self) -> None:
        self.closed = 0

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self.closed += 1

    def list_nodes(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="Meridian v4 / exit-a",
                is_disabled=False,
                is_connected=True,
            )
        ]

    def get_user(self, username: str) -> SimpleNamespace:
        return SimpleNamespace(
            username=username,
            short_uuid=f"short-{username}",
        )

    def fetch_subscription(
        self,
        short_uuid: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            url=f"https://panel.example/api/sub/{short_uuid}",
            content="dmxlc3M6Ly8=",
        )


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="exit-a-reality",
                        protocol="reality",
                        reality_sni="www.microsoft.com",
                    )
                ],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


def _drivers(plan: ResourcePlan) -> ResourceDrivers:
    driver = ContractResourceDriver()
    return cast(
        ResourceDrivers,
        {
            resource.payload.kind: driver
            for resource in plan.resources
            if resource.payload.kind not in {"control_plane_runtime", "probe"}
        },
    )


def test_reviewed_apply_resumes_to_a_no_op_and_verifies_canonical_subscription(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    registry.add(
        ServerEntry(
            host="198.51.100.10",
            name="control",
            id="srv-control",
            auth_state="validated",
        )
    )
    registry.add(
        ServerEntry(
            host="198.51.100.20",
            name="exit",
            id="srv-exit",
            auth_state="validated",
        )
    )
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://panel.example",
            api_token="token",
            server_ip="198.51.100.10",
        )
    )
    panel = _Panel()
    persists: list[str] = []
    bootstrap = MagicMock()
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=lambda state: persists.append(state.active_plan_hash),
        panel_factory=lambda _url, _token: cast(
            MeridianPanel,
            panel,
        ),
        connection_builder=lambda _entry: MagicMock(spec=ServerConnection),
        control_bootstrap=bootstrap,
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_remnawave_drivers",
        lambda context: _drivers(context.plan),
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_server_drivers",
        lambda context: _drivers(context.plan),
    )
    draft = SetupDraft.from_intent(_intent())
    plan_hash = runtime.review(draft).plan.plan_hash
    draft = draft.model_copy(
        update={
            "review_hash": plan_hash,
            "current_stage": "apply",
            "completed_stages": [
                "servers",
                "roles",
                "paths",
                "routing",
                "access",
                "review",
            ],
        }
    )

    first = runtime.apply(draft)
    second = runtime.apply(draft)
    before_inspection = copy.deepcopy({key: value for key, value in vars(cluster).items() if key != "_lock"})
    persist_count = len(persists)
    inspection = runtime.inspect_intent(_intent())

    assert first.all_succeeded, [
        (
            item.action.resource.logical_id,
            item.status,
            item.error,
        )
        for item in first.results
        if not item.succeeded
    ]
    assert first.changed
    assert second.all_succeeded and not second.changed
    assert inspection.converged
    assert inspection.drifted == []
    assert {key: value for key, value in vars(cluster).items() if key != "_lock"} == before_inspection
    assert len(persists) == persist_count
    assert cluster.active_plan_hash == plan_hash
    assert cluster.topology_intent == _intent()
    bootstrap.assert_not_called()
    applied = draft.model_copy(update={"applied_plan_hash": plan_hash})
    verification = runtime.verify(applied)
    assert verification.node_count == 1
    assert verification.subscription_urls == {"default": "https://panel.example/api/sub/short-default"}
    assert persists


def test_apply_rejects_unreviewed_hash_before_remote_work(
    tmp_path,
) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    runtime = SetupRuntime(registry)
    draft = SetupDraft.from_intent(_intent()).model_copy(update={"review_hash": "stale"})

    with pytest.raises(LocalStateError, match="no longer matches"):
        runtime.apply(draft)
