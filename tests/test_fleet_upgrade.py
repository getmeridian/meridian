"""`fleet upgrade` observes images read-only, applies only the reviewed plan, then checks the fleet."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands import fleet_upgrade
from meridian.config import REMNAWAVE_BACKEND_IMAGE, REMNAWAVE_NODE_IMAGE, REMNAWAVE_POSTGRES_IMAGE
from meridian.diagnostics.images import (
    ImageObservation,
    ServerImages,
    control_components,
    node_components,
    observe_images,
)
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.remnawave.versions import UPGRADABLE_PINS, effective_pins
from meridian.setup.single_server import single_server_intent
from tests.support.mock_connection import MockConnection

_OLD_NODE = "remnawave/node:3.4.0"


def _inspect_output(images: dict[str, str]) -> str:
    return "".join(f"{container}\t{image}\n" for container, image in images.items())


def test_images_are_read_with_docker_inspect_only() -> None:
    conn = MockConnection()
    conn.when(
        "docker inspect",
        stdout=_inspect_output(
            {
                "ctlsvc-api": REMNAWAVE_BACKEND_IMAGE,
                "ctlsvc-web": "remnawave/subscription-page:latest",
                "ctlsvc-db": "postgres:16.9-alpine",
                "ctlsvc-cache": "-",
                "netagent": _OLD_NODE,
            }
        ),
    )

    observed = observe_images(conn, control_components() + node_components(), server_ref="srv-a", label="control")

    (command,) = conn.calls
    assert "docker inspect --format '{{.Config.Image}}'" in command
    assert not any(verb in command for verb in ("pull", "compose", " rm ", "up -d"))
    assert [(image.component, image.status) for image in observed.images] == [
        ("backend", "current"),
        ("subscription_page", "kept_unmanaged"),
        ("postgres", "held_major"),
        ("valkey", "absent"),
        ("node", "upgrade"),
    ]
    assert [image.component for image in observed.changes] == ["valkey", "node"]


@pytest.mark.parametrize(
    ("rc", "stdout", "error"),
    [
        (127, "", "docker is not installed"),
        (255, "", "exit 255"),
        (0, "netagent\tx\n", "incomplete"),
    ],
)
def test_missing_evidence_is_unavailable_not_a_mismatch(rc: int, stdout: str, error: str) -> None:
    conn = MockConnection()
    conn.when("docker inspect", rc=rc, stdout=stdout)

    observed = observe_images(conn, control_components(), server_ref="srv-a", label="control")

    assert not observed.available and error in observed.error
    assert observed.images == ()


def test_inspection_script_reports_absent_and_pre_rename_containers_in_a_real_shell(tmp_path: Path) -> None:
    """A server not yet migrated reports its pre-rename node under the neutral name."""
    fake = tmp_path / "docker"
    fake.write_text(
        '#!/bin/sh\nfor last; do :; done\n[ "$last" = remnawave-node ] && echo remnawave/node:3.4.0 && exit 0\nexit 1\n'
    )
    fake.chmod(0o755)
    conn = MockConnection()
    observe_images(conn, control_components()[:1] + node_components(), server_ref="srv-a", label="a")
    script = conn.calls[0]

    result = subprocess.run(
        ["/bin/sh", "-c", script], env={"PATH": f"{tmp_path}:/usr/bin:/bin"}, capture_output=True, text=True
    )

    assert result.returncode == 0
    assert result.stdout == "ctlsvc-api\t-\nnetagent\tremnawave/node:3.4.0\n"


def _server(*images: ImageObservation, error: str = "") -> ServerImages:
    return ServerImages("srv-a", "control", tuple(images), error=error)


def _image(status: str, running: str = _OLD_NODE) -> ImageObservation:
    return ImageObservation("node", "netagent", REMNAWAVE_NODE_IMAGE, running, status)  # type: ignore[arg-type]


def _run(observations: list[list[ServerImages]], *, yes: bool = True) -> tuple[int, MagicMock, MagicMock]:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    with (
        patch.object(fleet_upgrade, "load_topology_cluster", return_value=cluster),
        patch.object(fleet_upgrade, "_target_pins", return_value=dict(UPGRADABLE_PINS)),
        patch.object(fleet_upgrade, "_save_pins"),
        patch.object(fleet_upgrade, "_observe", side_effect=observations),
        patch.object(fleet_upgrade, "_apply") as apply,
        patch("meridian.commands.health.run") as check,
        patch.object(fleet_upgrade, "confirm", return_value=False),
    ):
        try:
            fleet_upgrade.run_upgrade(yes=yes)
        except typer.Exit as exc:
            return int(exc.exit_code), apply, check
    return 0, apply, check


def test_a_current_fleet_changes_nothing() -> None:
    code, apply, check = _run([[_server(_image("current", REMNAWAVE_NODE_IMAGE), _image("kept_newer"))]])

    assert code == 0
    apply.assert_not_called()
    check.assert_not_called()


def test_unreadable_servers_stop_before_anything_changes() -> None:
    code, apply, _check = _run([[_server(_image("upgrade")), _server(error="ssh: connection refused")]])

    assert code == 3
    apply.assert_not_called()


def test_upgrades_apply_the_reviewed_plan_then_check_every_server() -> None:
    code, apply, check = _run([[_server(_image("upgrade"))], [_server(_image("current", REMNAWAVE_NODE_IMAGE))]])

    assert code == 0
    apply.assert_called_once()
    check.assert_called_once_with()


def test_declined_confirmation_changes_nothing() -> None:
    code, apply, _check = _run([[_server(_image("upgrade"))]], yes=False)

    assert code == 1
    apply.assert_not_called()


def test_an_image_still_behind_after_apply_is_a_system_failure() -> None:
    code, apply, check = _run([[_server(_image("upgrade"))], [_server(_image("upgrade"))]])

    assert code == 3
    apply.assert_called_once()
    check.assert_not_called()


def test_a_held_postgres_major_is_explained_and_never_applied(capsys: pytest.CaptureFixture[str]) -> None:
    held = ImageObservation("postgres", "ctlsvc-db", REMNAWAVE_POSTGRES_IMAGE, "postgres:16.9-alpine", "held_major")

    code, apply, _check = _run([[_server(held)]])

    assert code == 0
    apply.assert_not_called()
    output = capsys.readouterr().err
    assert "PostgreSQL 16 stays" in output and "pg_dumpall" in output


def test_pending_removals_must_be_applied_first() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(
        retiring=(ManagedResourceBinding(logical_id="realm:old:0", resource_kind="realm_hop"),), review_hash="h"
    )

    with patch("meridian.setup.runtime.SetupRuntime", return_value=runtime), pytest.raises(typer.Exit) as exc_info:
        fleet_upgrade._apply(cluster, MagicMock(), cluster.topology_intent)  # type: ignore[arg-type]

    assert exc_info.value.exit_code == 2
    runtime.apply_intent.assert_not_called()


def test_reviewed_apply_binds_the_review_hash() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    runtime = MagicMock()
    runtime.review.return_value = SimpleNamespace(retiring=(), review_hash="sha256:review")
    runtime.apply_intent.return_value = ResourceExecutionResult(plan_hash="p", generation=2)

    with patch("meridian.setup.runtime.SetupRuntime", return_value=runtime):
        fleet_upgrade._apply(cluster, MagicMock(), cluster.topology_intent)  # type: ignore[arg-type]

    assert runtime.apply_intent.call_args.kwargs["expected_plan_hash"] == "sha256:review"


def test_components_follow_the_compiled_roles(servers_file: Path) -> None:
    from meridian.servers import ServerEntry, ServerRegistry

    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="control", id="srv-a"))
    seen: dict[str, tuple[str, ...]] = {}

    def fake_observe(conn: object, components: tuple, *, server_ref: str, label: str) -> ServerImages:
        seen[server_ref] = tuple(component.component for component in components)
        return ServerImages(server_ref, label)

    with (
        patch.object(fleet_upgrade, "observe_images", side_effect=fake_observe),
        patch("meridian.resolve.connection_for_entry"),
    ):
        fleet_upgrade._observe(registry, single_server_intent(None, server_ref="srv-a"))

    assert seen == {"srv-a": ("backend", "subscription_page", "postgres", "valkey", "node")}


_NEWER_NODE = "remnawave/node:3.9.1@sha256:" + "a" * 64


def test_a_saved_newer_release_raises_the_pin_and_never_lowers_it() -> None:
    assert effective_pins({"remnawave_node_image": _NEWER_NODE})["remnawave_node_image"] == _NEWER_NODE
    older = "remnawave/node:3.0.0@sha256:" + "b" * 64
    assert effective_pins({"remnawave_node_image": older})["remnawave_node_image"] == REMNAWAVE_NODE_IMAGE
    # Another major, a missing digest, or an unknown component is never trusted from cluster.yml.
    for name, image in (
        ("remnawave_node_image", "remnawave/node:4.0.0@sha256:" + "c" * 64),
        ("remnawave_node_image", "remnawave/node:3.9.9"),
        ("remnawave_postgres_image", "postgres:18.0-alpine@sha256:" + "d" * 64),
    ):
        assert effective_pins({name: image}) == dict(UPGRADABLE_PINS)


def test_saved_pins_reach_the_reviewed_runtime_contract(servers_file: Path) -> None:
    from meridian.core.setup import SetupDraft
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.runtime import SetupRuntime

    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="control", id="srv-a"))
    cluster = ClusterConfig(
        topology_intent=single_server_intent(None, server_ref="srv-a", address="198.51.100.10"),
        image_pins={"remnawave_node_image": _NEWER_NODE},
    )

    review = SetupRuntime(registry, cluster_loader=lambda: cluster).review(
        SetupDraft.from_intent(cluster.topology_intent)  # type: ignore[arg-type]
    )

    assert review.plan.deployment_contract.runtime_pins["remnawave_node_image"] == _NEWER_NODE


def test_image_pins_survive_a_cluster_round_trip(tmp_home: Path) -> None:
    cluster = ClusterConfig(image_pins={"remnawave_node_image": _NEWER_NODE})
    cluster.save()

    assert ClusterConfig.load().image_pins == {"remnawave_node_image": _NEWER_NODE}


def test_latest_release_stays_in_the_major_and_skips_prereleases() -> None:
    from meridian.remnawave import registry

    tags = ["3.4.2", "3.4.10", "3.5.0-rc1", "3", "latest", "4.0.0", "3.10.1"]
    calls: list[str] = []

    def fake_get(url: str, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
        calls.append(url)
        return (b'{"token": "t"}' if "auth.docker.io" in url else json.dumps({"tags": tags}).encode()), {}

    with (
        patch.object(registry, "_get", side_effect=fake_get),
        patch.object(registry, "resolve_index_digest", return_value=("remnawave/node:3.10.1", "sha256:" + "e" * 64)),
    ):
        assert registry.latest_release(REMNAWAVE_NODE_IMAGE) == "remnawave/node:3.10.1@sha256:" + "e" * 64

    with (
        patch.object(registry, "_get", side_effect=fake_get),
        patch.object(registry, "resolve_index_digest") as resolve,
    ):
        tags[:] = ["3.4.2", "3.4.1"]
        assert registry.latest_release(REMNAWAVE_NODE_IMAGE) == REMNAWAVE_NODE_IMAGE
        resolve.assert_not_called()


def test_tested_upgrade_skips_docker_hub() -> None:
    cluster = ClusterConfig(image_pins={"remnawave_node_image": _NEWER_NODE})

    with patch("meridian.remnawave.registry.latest_release") as latest:
        assert fleet_upgrade._target_pins(cluster, tested=True) == dict(UPGRADABLE_PINS)
    latest.assert_not_called()
