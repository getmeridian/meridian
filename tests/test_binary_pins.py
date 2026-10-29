"""Realm pins: `fleet upgrade` records newer same-major releases by published digest, never lowers them."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.binary_pins import UPGRADABLE_BINARIES, BinaryPin, effective_binary_pins
from meridian.cluster import ClusterConfig
from meridian.commands import fleet_upgrade
from meridian.config import REALM_SHA256, REALM_VERSION
from meridian.core.topology import TransparentRelayIntent
from meridian.github_releases import ReleaseError, latest_binary_release
from meridian.provision.relay import InstallRealm, RelayContext
from meridian.setup.single_server import single_server_intent
from tests.support.mock_connection import MockConnection
from tests.support.topology import single_exit_intent

_TARGETS = sorted(REALM_SHA256)
_NEWER = BinaryPin("2.99.0", {target: "a" * 64 for target in _TARGETS})


def _relay_intent():  # type: ignore[no-untyped-def]
    return single_exit_intent(
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-a",
                protocol_path_ref="reality-a",
                reality_sni="relay-a.example.com",
            )
        ]
    )


def test_a_saved_newer_realm_raises_the_pin_and_never_lowers_it() -> None:
    assert effective_binary_pins({"realm": _NEWER})["realm"] == _NEWER
    older = BinaryPin("2.0.0", {target: "b" * 64 for target in _TARGETS})
    assert effective_binary_pins({"realm": older}) == UPGRADABLE_BINARIES


@pytest.mark.parametrize(
    ("name", "pin"),
    [
        ("realm", BinaryPin("3.0.0", {target: "c" * 64 for target in _TARGETS})),
        ("realm", BinaryPin("2.99.0", {_TARGETS[0]: "c" * 64})),
        ("realm", BinaryPin("2.99.0", {target: "not-a-digest" for target in _TARGETS})),
        ("realm", BinaryPin("2.99.0-2", {target: "c" * 64 for target in _TARGETS})),
        ("acme", BinaryPin("9.9.9", {"acme.sh": "c" * 64})),
    ],
    ids=["other-major", "missing-asset", "bad-digest", "suffixed-version", "unknown-binary"],
)
def test_untrusted_saved_pins_fall_back_to_the_cli_pin(name: str, pin: BinaryPin) -> None:
    assert effective_binary_pins({name: pin}) == UPGRADABLE_BINARIES


def test_binary_pins_survive_a_cluster_round_trip(tmp_home: Path) -> None:
    ClusterConfig(binary_pins={"realm": _NEWER}).save()

    assert ClusterConfig.load().binary_pins == {"realm": _NEWER}


def _release(tag: str, *, prerelease: bool = False, draft: bool = False, digests: bool = True) -> dict[str, object]:
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {"name": f"realm-{target}.tar.gz", "digest": f"sha256:{'d' * 64}" if digests else None}
            for target in _TARGETS
        ],
    }


def test_latest_release_stays_in_the_major_and_takes_the_published_digests() -> None:
    releases = [
        _release("v3.0.0"),
        _release("v2.99.5", prerelease=True),
        _release("v2.99.4", draft=True),
        _release("v2.99.3-2"),
        _release("v2.99.2"),
        _release("v2.9.0"),
    ]
    with patch("meridian.github_releases._releases", return_value=releases):
        pin = latest_binary_release("realm", UPGRADABLE_BINARIES["realm"])

    assert pin == BinaryPin("2.99.2", {target: "d" * 64 for target in _TARGETS})


def test_nothing_newer_keeps_the_current_pin() -> None:
    with patch("meridian.github_releases._releases", return_value=[_release(f"v{REALM_VERSION}")]):
        assert latest_binary_release("realm", UPGRADABLE_BINARIES["realm"]) is UPGRADABLE_BINARIES["realm"]


def test_a_release_without_published_digests_is_refused() -> None:
    with (
        patch("meridian.github_releases._releases", return_value=[_release("v2.99.0", digests=False)]),
        pytest.raises(ReleaseError, match="publishes no sha256"),
    ):
        latest_binary_release("realm", UPGRADABLE_BINARIES["realm"])


def test_a_saved_realm_pin_reaches_the_reviewed_runtime_contract(servers_file: Path) -> None:
    from meridian.core.setup import SetupDraft
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.runtime import SetupRuntime

    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="control", id="srv-a"))
    cluster = ClusterConfig(
        topology_intent=single_server_intent(None, server_ref="srv-a", address="198.51.100.10"),
        binary_pins={"realm": _NEWER},
    )

    review = SetupRuntime(registry, cluster_loader=lambda: cluster).review(
        SetupDraft.from_intent(cluster.topology_intent)  # type: ignore[arg-type]
    )

    pins = review.plan.deployment_contract.runtime_pins
    assert pins["realm_version"] == "2.99.0"
    assert {name: value for name, value in pins.items() if name.startswith("realm_sha256.")} == {
        f"realm_sha256.{target}": "a" * 64 for target in _TARGETS
    }


def test_realm_install_refuses_a_target_without_a_pinned_digest() -> None:
    conn = MockConnection()
    conn.when("netfwd --version", rc=127)
    conn.when("uname -m", stdout="x86_64\n")
    conn.when("sha256sum", stdout="e" * 64)
    ctx = RelayContext(relay_ip="198.51.100.10", exit_ip="198.51.100.1", realm_version="2.99.0", realm_sha256={})

    result = InstallRealm().run(conn, ctx)

    assert result.status == "failed" and "no checksum pinned" in result.detail
    assert not any("install -m 755" in command for command in conn.calls)


def test_fleets_without_relays_never_ask_github() -> None:
    cluster = ClusterConfig(topology_intent=single_exit_intent())

    with patch("meridian.github_releases.latest_binary_release") as latest:
        assert fleet_upgrade._binary_targets(cluster, single_exit_intent(), tested=False) == {}
    latest.assert_not_called()


def test_tested_upgrade_skips_github_and_keeps_a_saved_realm() -> None:
    cluster = ClusterConfig(binary_pins={"realm": _NEWER})

    with patch("meridian.github_releases.latest_binary_release") as latest:
        assert fleet_upgrade._binary_targets(cluster, _relay_intent(), tested=True) == {"realm": (_NEWER, _NEWER)}
    latest.assert_not_called()


def _relay(running: str) -> fleet_upgrade.RelayRealm:
    return fleet_upgrade.RelayRealm("srv-r", "relay", running)


def test_a_newer_realm_is_confirmed_saved_and_applied() -> None:
    cluster = ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))
    default = UPGRADABLE_BINARIES["realm"]
    with (
        patch.object(fleet_upgrade, "load_topology_cluster", return_value=cluster),
        patch.object(fleet_upgrade, "_binary_targets", return_value={"realm": (default, _NEWER)}),
        patch.object(fleet_upgrade, "_observe_service", return_value=[]),
        patch.object(
            fleet_upgrade,
            "_observe_relays",
            side_effect=[[_relay(default.version)], [_relay(default.version)], [_relay(_NEWER.version)]],
        ),
        patch.object(fleet_upgrade, "_save_pins") as save,
        patch.object(fleet_upgrade, "_apply") as apply,
        patch("meridian.commands.health.run"),
        patch.object(fleet_upgrade, "confirm", return_value=False),
    ):
        with pytest.raises(typer.Exit):
            fleet_upgrade.run_upgrade()
        save.assert_not_called()
        apply.assert_not_called()

        fleet_upgrade.run_upgrade(yes=True)

    save.assert_called_once_with(cluster, {"realm": _NEWER})
    apply.assert_called_once()


@pytest.mark.parametrize(
    ("running", "applies"),
    [(_NEWER.version, False), (REALM_VERSION, True), ("", True)],
    ids=["current", "behind-its-saved-pin", "missing-or-broken"],
)
def test_a_saved_realm_pin_is_compared_with_what_the_relay_runs(running: str, applies: bool) -> None:
    """A failed upgrade saves the pin first; rerunning must still finish the relay."""
    cluster = ClusterConfig(
        topology_intent=single_server_intent(None, server_ref="srv-a"), binary_pins={"realm": _NEWER}
    )
    with (
        patch.object(fleet_upgrade, "load_topology_cluster", return_value=cluster),
        patch.object(fleet_upgrade, "_binary_targets", return_value={"realm": (_NEWER, _NEWER)}),
        patch.object(fleet_upgrade, "_observe_service", return_value=[]),
        patch.object(fleet_upgrade, "_observe_relays", side_effect=[[_relay(running)], [_relay(_NEWER.version)]]),
        patch.object(fleet_upgrade, "_save_pins"),
        patch.object(fleet_upgrade, "_apply") as apply,
        patch("meridian.commands.health.run"),
    ):
        fleet_upgrade.run_upgrade(yes=True)

    assert apply.called is applies


def test_bump_script_rewrites_each_pin_once_and_reports_unchecked_sources(tmp_path: Path) -> None:
    from scripts import bump_pins

    config = tmp_path / "config.py"
    config.write_text(f'REALM_VERSION = "{REALM_VERSION}"\nA = "{"1" * 64}"\nB = "{"2" * 64}"\n', encoding="utf-8")
    bump = bump_pins.Bump(
        "Realm",
        REALM_VERSION,
        "2.99.0",
        (
            (f'REALM_VERSION = "{REALM_VERSION}"', 'REALM_VERSION = "2.99.0"'),
            ("1" * 64, "3" * 64),
            ("2" * 64, "4" * 64),
        ),
    )

    def offline() -> None:
        raise ReleaseError("GitHub request failed")

    summary = tmp_path / "pr.md"
    with (
        patch.object(bump_pins, "CONFIG", config),
        patch.object(bump_pins, "checks", return_value=[("Realm", lambda: bump), ("acme.sh", offline)]),
    ):
        assert bump_pins.main(["--write", "--summary", str(summary)]) == 1

    assert config.read_text(encoding="utf-8") == f'REALM_VERSION = "2.99.0"\nA = "{"3" * 64}"\nB = "{"4" * 64}"\n'
    assert "| Realm | " in summary.read_text(encoding="utf-8")
    assert "acme.sh: GitHub request failed" in summary.read_text(encoding="utf-8")
