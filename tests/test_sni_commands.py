"""`node sni` and `relay sni` replace a camouflage SNI as a reviewed intent edit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands.sni import run_exit_sni, run_relay_sni
from meridian.compiler.models import InboundPayload
from meridian.core.topology import DeliveryIntent, SetupIntent
from meridian.diagnostics.camouflage import OwnHostname, camouflage_refusal
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import set_exit_reality_sni, set_relay_reality_sni
from tests.support.topology import compile_panel_pass
from tests.test_topology_removal_commands import SERVERS, _intent


class _Runtime:
    def __init__(self) -> None:
        self.applied: tuple[SetupIntent, str] | None = None

    def review(self, draft: object, *, cluster: object) -> SimpleNamespace:
        retiring = (ManagedResourceBinding(logical_id="inbound:exit-b:old", resource_kind="inbound", active=True),)
        return SimpleNamespace(retiring=retiring, review_hash="sha256:review")

    def apply_intent(self, intent: SetupIntent, *, expected_plan_hash: str) -> ResourceExecutionResult:
        self.applied = (intent, expected_plan_hash)
        return ResourceExecutionResult(plan_hash="sha256:plan", generation=2)


@pytest.fixture
def registry(servers_file: Path) -> ServerRegistry:
    saved = ServerRegistry(servers_file)
    for server_id, (host, name) in SERVERS.items():
        saved.add(ServerEntry(host=host, name=name, id=server_id))
    return saved


def _own_sni(intent: SetupIntent, relay_id: str, sni: str) -> SetupIntent:
    return intent.model_copy(
        update={
            "transparent_relays": [
                relay.model_copy(update={"reality_sni": sni}) if relay.id == relay_id else relay
                for relay in intent.transparent_relays
            ]
        }
    )


def _run(
    command: str,
    target: str,
    sni: str,
    *,
    intent: SetupIntent | None = None,
    reachable: bool = True,
    yes: bool = True,
    confirmed: bool = True,
    self_steal: bool = False,
    dns: OwnHostname | None = None,
) -> tuple[int, _Runtime, list[str]]:
    """Run a command; returns its exit code, the runtime, and the hosts asked to reach the SNI."""
    runtime = _Runtime()
    probed: list[str] = []

    def connect(entry: ServerEntry) -> MagicMock:
        conn = MagicMock()
        conn.host = entry.host
        return conn

    def reach(conn: MagicMock, candidates: tuple[str, ...]) -> list[str]:
        probed.append(conn.host)
        return list(candidates) if reachable else []

    with (
        patch(
            "meridian.commands.sni.load_topology_cluster",
            return_value=ClusterConfig(topology_intent=intent or _intent()),
        ),
        patch("meridian.resolve.connection_for_entry", side_effect=connect),
        patch("meridian.diagnostics.preflight.reachable_reality_targets", side_effect=reach),
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
        patch("meridian.commands.sni.confirm", return_value=confirmed),
        patch(
            "meridian.diagnostics.camouflage.own_hostname",
            return_value=dns or OwnHostname("here", ("198.51.100.11",)),
        ),
    ):
        try:
            if command == "node":
                run_exit_sni(target, sni, self_steal=self_steal, yes=yes)
            else:
                run_relay_sni(target, sni, yes=yes)
        except typer.Exit as exc:
            return int(exc.exit_code), runtime, probed
    return 0, runtime, probed


def test_exit_sni_edit_touches_only_the_reality_path() -> None:
    updated = set_exit_reality_sni(_intent(), "exit-b", "new.example.net")

    paths = {exit_.id: [path.reality_sni for path in exit_.paths] for exit_ in updated.exits}
    assert paths == {"exit-a": ["www.example.com"], "exit-b": ["new.example.net"]}
    assert updated.transparent_relays == _intent().transparent_relays


def test_relay_sni_edit_stores_its_own_name_or_follows_the_exit_again() -> None:
    own = set_relay_reality_sni(_intent(), {"relay-a"}, "new.example.net")
    assert own.transparent_relays[0].reality_sni == "new.example.net"

    back = set_relay_reality_sni(own, {"relay-a"}, "www.example.org")
    assert back.transparent_relays[0].reality_sni == ""


def test_relay_sni_edit_refuses_unknown_chains() -> None:
    with pytest.raises(ValueError, match="relay-zz"):
        set_relay_reality_sni(_intent(), {"relay-zz"}, "new.example.net")


def test_compiled_plan_carries_only_the_new_exit_sni() -> None:
    plan = compile_panel_pass(set_exit_reality_sni(_intent(), "exit-b", "new.example.net"))

    inbounds = [
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, InboundPayload)
        and resource.payload.workload_ref == "exit-b"
        and resource.payload.protocol == "reality"
    ]
    assert inbounds and all(inbound.reality_server_names == ["new.example.net"] for inbound in inbounds)
    # No grace period: the old name leaves every listener and route (the relay follows the exit).
    assert "www.example.org" not in plan.model_dump_json()


def test_compiled_plan_gives_a_relay_with_its_own_sni_a_listener_for_it() -> None:
    plan = compile_panel_pass(set_relay_reality_sni(_intent(), {"relay-a"}, "new.example.net"))

    relay_inbounds = [
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, InboundPayload) and resource.payload.purpose == "relay"
    ]
    assert [inbound.reality_server_names for inbound in relay_inbounds] == [["new.example.net"]]


def test_node_sni_checks_the_exit_then_applies_the_review(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    code, runtime, probed = _run("node", "exit-b-host", "New.Example.NET")

    assert code == 0 and runtime.applied is not None
    updated, approved = runtime.applied
    assert approved == "sha256:review"
    assert next(exit_ for exit_ in updated.exits if exit_.id == "exit-b").paths[0].reality_sni == "new.example.net"
    assert probed == ["198.51.100.12"]
    output = capsys.readouterr().err
    assert "www.example.org → new.example.net" in output
    assert "change with it: relay-a" in output
    assert "remove inbound:exit-b:old" in output
    assert "every 6 hours" in output


@pytest.mark.parametrize(("command", "target"), [("node", "exit-b"), ("relay", "relay-a")])
def test_sni_review_reports_the_reviewed_refresh_interval(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str], command: str, target: str
) -> None:
    intent = _intent().model_copy(update={"delivery": DeliveryIntent(update_interval_hours=24)})
    code, runtime, _probed = _run(command, target, "new.example.net", intent=intent)
    assert code == 0 and runtime.applied is not None
    assert "every 24 hours" in capsys.readouterr().err


def test_node_sni_names_relays_with_their_own_sni_and_gateways(
    registry: ServerRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _runtime, _probed = _run(
        "node", "exit-b", "new.example.net", intent=_own_sni(_intent(), "relay-a", "x.example.net")
    )
    assert code == 0
    assert "keep it: relay-a" in capsys.readouterr().err

    code, _runtime, probed = _run("node", "exit-a", "new.example.net")
    assert code == 0
    assert "change too: gateway-a" in capsys.readouterr().err
    assert probed == ["198.51.100.11", "198.51.100.15"]  # the exit, then the gateway built on its path


def test_relay_sni_checks_the_relays_exit(registry: ServerRegistry) -> None:
    code, runtime, probed = _run("relay", "relay-edge", "new.example.net")

    assert code == 0 and runtime.applied is not None
    assert runtime.applied[0].transparent_relays[0].reality_sni == "new.example.net"
    assert probed == ["198.51.100.12"]


def test_relay_sni_back_to_the_exits_name_skips_the_reachability_check(registry: ServerRegistry) -> None:
    code, runtime, probed = _run(
        "relay", "relay-a", "www.example.org", intent=_own_sni(_intent(), "relay-a", "x.example.net")
    )

    assert code == 0 and runtime.applied is not None
    assert runtime.applied[0].transparent_relays[0].reality_sni == ""
    assert probed == []


@pytest.mark.parametrize(
    ("command", "target", "sni", "intent"),
    [
        ("node", "exit-b", "www.example.org", None),
        ("relay", "relay-a", "www.example.org", None),
        ("relay", "relay-edge", "x.example.net", _own_sni(_intent(), "relay-a", "x.example.net")),
    ],
    ids=["exit", "inheriting-relay", "relay-own-sni"],
)
def test_an_unchanged_sni_is_a_no_op(
    registry: ServerRegistry, command: str, target: str, sni: str, intent: SetupIntent | None
) -> None:
    code, runtime, probed = _run(command, target, sni, intent=intent)

    assert (code, runtime.applied, probed) == (0, None, [])


@pytest.mark.parametrize("command", ["node", "relay"])
@pytest.mark.parametrize(
    "sni",
    ["www.apple.com", "apple.com", "icloud.com", "www.microsoft.com", "not a host", "localhost"],
)
def test_unusable_snis_are_refused_before_connecting(registry: ServerRegistry, command: str, sni: str) -> None:
    target = "exit-b" if command == "node" else "relay-a"

    code, runtime, probed = _run(command, target, sni)

    assert (code, runtime.applied, probed) == (2, None, [])


@pytest.mark.parametrize(("command", "target"), [("node", "exit-zz"), ("node", "gateway"), ("relay", "relay-zz")])
def test_unknown_targets_are_refused(registry: ServerRegistry, command: str, target: str) -> None:
    code, runtime, probed = _run(command, target, "new.example.net")

    assert (code, runtime.applied, probed) == (2, None, [])


def test_an_sni_the_exit_cannot_reach_is_refused(registry: ServerRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    code, runtime, _probed = _run("node", "exit-b", "new.example.net", reachable=False)

    assert code == 2 and runtime.applied is None
    assert "meridian scan exit-b-host" in capsys.readouterr().err


def test_declining_the_review_changes_nothing(registry: ServerRegistry) -> None:
    code, runtime, _probed = _run("node", "exit-b", "new.example.net", yes=False, confirmed=False)

    assert code == 1 and runtime.applied is None


@pytest.mark.parametrize(
    ("sni", "reason"),
    [
        ("www.apple.com", "network"),
        ("ICLOUD.com.", "network"),
        ("www.microsoft.com", "certificate"),
        ("learn.microsoft.com", ""),
        ("pineapple.com", ""),
        ("www.google.com", ""),
    ],
)
def test_camouflage_refusal_names_why(sni: str, reason: str) -> None:
    assert camouflage_refusal(sni) == reason


def test_self_steal_sni_checks_dns_instead_of_reaching_a_site(registry: ServerRegistry) -> None:
    """The exit serves its own name only after the apply, so the TLS reachability check cannot pass yet."""
    code, runtime, probed = _run("node", "exit-b", "www.example.net", self_steal=True)

    assert (code, probed) == (0, [])
    assert runtime.applied is not None
    [path] = [path for exit_ in runtime.applied[0].exits if exit_.id == "exit-b" for path in exit_.paths]
    assert (path.reality_sni, path.self_steal) == ("www.example.net", True)


def test_self_steal_sni_refuses_a_name_that_does_not_resolve(registry: ServerRegistry) -> None:
    code, runtime, _ = _run("node", "exit-b", "www.example.net", self_steal=True, dns=OwnHostname("unresolved"))

    assert code == 2
    assert runtime.applied is None


def test_self_steal_sni_pointing_elsewhere_still_applies(registry: ServerRegistry) -> None:
    """A proxied record still gets a certificate through the CDN; it only loses the matching address."""
    code, runtime, _ = _run(
        "node", "exit-b", "www.example.net", self_steal=True, dns=OwnHostname("elsewhere", ("203.0.113.5",))
    )

    assert code == 0
    assert runtime.applied is not None


def test_turning_self_steal_on_for_the_same_name_is_a_change(registry: ServerRegistry) -> None:
    code, runtime, _ = _run("node", "exit-b", "www.example.org", self_steal=True)

    assert code == 0
    assert runtime.applied is not None
