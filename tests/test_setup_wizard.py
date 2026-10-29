"""Presentation tests for resumable V4 setup navigation."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest

from meridian.cluster import ManagedResourceBinding
from meridian.commands.setup_wizard import OWN_DOMAIN, CamouflageSites, SetupWizard, _own_hostname_value
from meridian.compiler import compile_topology
from meridian.core.defaults import REALITY_SNI_CANDIDATES
from meridian.core.setup import SetupDraft
from meridian.diagnostics.camouflage import OwnHostname
from meridian.handoff import HandoffLinks
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.persistence import SetupDraftStore
from meridian.setup.runtime import (
    SetupReview,
    SetupRuntime,
    SetupVerification,
)
from meridian.setup.service import SetupDraftService
from meridian.setup.shelf import ServerShelf
from tests.support.topology import single_exit_intent


class _ScriptedIO:
    """Answers prompts in order; each answer names a fragment its prompt must contain."""

    def __init__(self, answers: Sequence[tuple[str, str]]) -> None:
        self.answers = list(answers)
        self.output: list[str] = []
        self.labels: list[str] = []

    def write(self, message: str = "") -> None:
        self.output.append(message)

    def ask(self, label: str, *, default: str = "") -> str:
        assert self.answers, f"Unexpected prompt: {label}"
        fragment, answer = self.answers.pop(0)
        assert fragment in label, f"Expected a prompt containing {fragment!r}, got {label!r}"
        self.labels.append(label)
        return answer or default


# control server runs only the panel; the second server is the exit.
_TWO_SERVER_JOURNEY: list[tuple[str, str]] = [
    ("Which servers should this VPN run on", "all"),
    ("control panel", "1"),
    ("Job for control", "control"),
    ("Job for exit", ""),
    ("Country code", ""),
    ("WARP", ""),
    ("Refuse Russian sites", ""),
    ("Domain for the control panel", ""),
    ("Name of this VPN", ""),
    ("Camouflage site", ""),
    ("Domain pointed at", ""),
    ("Names of the people", ""),
]


class _Runtime:
    def __init__(self, retiring: tuple[ManagedResourceBinding, ...] = ()) -> None:
        self.retiring = retiring

    def review(self, draft: SetupDraft) -> SetupReview:
        intent = draft.to_intent()
        return SetupReview(
            intent=intent,
            plan=compile_topology(intent),
            retiring=self.retiring,
        )

    def apply(self, draft: SetupDraft) -> SimpleNamespace:
        return SimpleNamespace(
            all_succeeded=True,
            changed=True,
            failed=[],
            results=[object()],
            plan_hash=self.review(draft).plan.plan_hash,
            review_hash=draft.review_hash,
        )

    def verify(self, draft: SetupDraft) -> SetupVerification:
        return SetupVerification(
            plan_hash=draft.applied_plan_hash,
            node_count=1,
            subscription_urls={"default": "https://panel.example/pages/sub/default"},
        )

    def publish_handoff(self, usernames):
        return [
            HandoffLinks(
                name,
                subscription_url=f"https://panel.example/pages/sub/{name}",
                page_url=f"https://panel.example/pages/{name}-uuid/",
            )
            for name in usernames
        ]


def _services(tmp_path):
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
    store = SetupDraftStore(tmp_path / "setup.json")
    shelf = ServerShelf(registry)
    return store, shelf, SetupDraftService(store, shelf)


def test_guided_journey_persists_every_stage_and_hands_off_subscription(
    tmp_path,
) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO([*_TWO_SERVER_JOURNEY, ("Set this up now?", "yes")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
    )

    outcome = wizard.run()

    assert outcome == "completed"
    assert io.answers == []
    draft = store.load()
    assert draft is not None
    assert draft.current_stage == "handoff"
    assert draft.roles is not None and all(exit_.geo_block for exit_ in draft.roles.exits)
    assert draft.roles.control.server_ref == "srv-control"
    assert [exit_.server_ref for exit_ in draft.roles.exits] == ["srv-exit"]
    assert draft.completed_stages == list(
        (
            "servers",
            "roles",
            "paths",
            "routing",
            "access",
            "review",
            "apply",
            "verify",
            "handoff",
        )
    )
    assert draft.review_hash == draft.applied_plan_hash
    assert any("https://panel.example/pages/default-uuid/" in line for line in io.output)
    assert any("https://panel.example/pages/sub/default" in line for line in io.output)


def test_review_lists_removals_and_approves_the_retirement_bound_hash(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    retiring = (
        ManagedResourceBinding(
            logical_id="realm:old-relay:0",
            resource_kind="realm_hop",
            generation=1,
            remote_id="meridian-realm-0123456789abcdef",
            active=True,
            server_ref="srv-old-relay",
        ),
    )
    io = _ScriptedIO([*_TWO_SERVER_JOURNEY, ("Set this up now?", "")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime(retiring)),
        register_server=lambda *_args: None,
        io=io,
    )

    assert wizard.run() == "completed"

    draft = store.load()
    assert draft is not None
    assert draft.review_hash != draft.applied_plan_hash
    assert draft.applied_plan_hash == compile_topology(draft.to_intent()).plan_hash
    assert any("removes 1 resources" in line for line in io.output)
    assert any("realm:old-relay:0" in line for line in io.output)


def test_details_repeats_prompt_and_quit_keeps_progress_file_untouched(
    tmp_path,
) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO([("Which servers should this VPN run on", "details"), ("Which servers", "quit")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
    )

    assert wizard.run() == "saved"
    assert store.load() is None
    assert any("never stores passwords" in line for line in io.output)


def test_back_from_review_invalidates_access_and_review_hash(
    tmp_path,
) -> None:
    store, shelf, service = _services(tmp_path)
    seed_io = _ScriptedIO([*_TWO_SERVER_JOURNEY, ("Set this up now?", "save")])
    seed = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=seed_io,
    )
    assert seed.run() == "saved"
    draft = store.load()
    assert draft is not None and draft.current_stage == "review"

    io = _ScriptedIO([("Set this up now?", "back"), ("Names of the people", "save")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
    )
    assert wizard.run() == "saved"

    rewound = store.load()
    assert rewound is not None
    assert rewound.current_stage == "access"
    assert rewound.access is None
    assert rewound.review_hash == ""


def _wizard(service, shelf, io, register_server=lambda *_args: None) -> SetupWizard:
    return SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=register_server,
        io=io,
    )


def test_first_server_added_as_local_is_selected_and_becomes_control_and_exit(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    store = SetupDraftStore(tmp_path / "setup.json")
    shelf = ServerShelf(registry)
    service = SetupDraftService(store, shelf)
    registered: list[tuple[str, str, str, int]] = []

    def register(address: str, name: str, user: str, port: int) -> None:
        registered.append((address, name, user, port))
        registry.add(ServerEntry(host="198.51.100.30", name=name, id="srv-here", auth_state="validated"))

    io = _ScriptedIO(
        [
            ("Server address", "local"),
            ("Short name", ""),
            ("Add another server?", ""),
            ("Country code", "de"),
            ("WARP", ""),
            ("Refuse Russian sites", "no"),
            ("Domain for the control panel", ""),
            ("Name of this VPN", ""),
            ("Camouflage site", ""),
            ("Domain pointed at", ""),
            ("Names of the people", "alice, laptop"),
            ("Set this up now?", ""),
        ]
    )

    assert _wizard(service, shelf, io, register).run() == "completed"

    assert registered == [("local", "this-server", "", 0)]
    draft = store.load()
    assert draft is not None and draft.roles is not None and draft.access is not None
    assert draft.roles.control.server_ref == "srv-here"
    assert [(exit_.server_ref, exit_.region, exit_.geo_block) for exit_ in draft.roles.exits] == [
        ("srv-here", "DE", False)
    ]
    assert draft.access.access.users == ["alice", "laptop"]
    assert any("this-server will run the control panel" in line for line in io.output)


def test_relay_forwards_to_the_only_exit_without_asking(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [
            ("Which servers should this VPN run on", "all"),
            ("control panel", "2"),
            ("Job for control", "relay"),
            ("Job for exit", "exit"),
            ("Country code", ""),
            ("WARP", ""),
            ("Refuse Russian sites", ""),
            ("Domain for the control panel", ""),
            ("Name of this VPN", ""),
            ("Camouflage site", ""),
            ("Domain pointed at", ""),
            ("Names of the people", ""),
            ("Set this up now?", "save"),
        ]
    )

    assert _wizard(service, shelf, io).run() == "saved"

    draft = store.load()
    assert draft is not None and draft.roles is not None
    (relay,) = draft.roles.transparent_relays
    assert relay.hop_server_refs == ["srv-control"]
    assert relay.exit_ref == "exit"
    assert any("Relay" in line and "control (198.51.100.10)" in line for line in io.output)


@pytest.mark.parametrize(
    ("answer", "action", "target_ref", "shown"),
    [
        ("1", "route", "exit", "exit"),
        ("2", "direct", "", "directly from the gateway"),
        ("3", "block", "", "nowhere (block it)"),
    ],
    ids=["exit", "direct-from-gateway", "block"],
)
def test_gateway_rules_pick_an_exit_direct_or_block_and_review_shows_them(
    tmp_path, answer: str, action: str, target_ref: str, shown: str
) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [
            ("Which servers should this VPN run on", "all"),
            ("control panel", "1"),
            ("Job for control", "gateway"),
            ("Job for exit", ""),
            ("Country code", ""),
            ("WARP", ""),
            ("Refuse Russian sites", ""),
            ("Domain for the control panel", ""),
            ("Name of this VPN", ""),
            ("Camouflage site", ""),
            ("Domain pointed at", ""),
            ("Where should traffic go by default?", ""),
            ("Add a rule", "yes"),
            ("Match traffic by", "domain"),
            ("Values", "example.org"),
            ("Where should this traffic go?", answer),
            ("Add a rule", ""),
            ("Names of the people", ""),
            ("Set this up now?", "save"),
        ]
    )

    assert _wizard(service, shelf, io).run() == "saved"

    rules = [line for line in io.output if "Rule:" in line]
    assert [line.split("Rule:")[1].strip() for line in rules] == [
        f"example.org → {shown}",
        "everything else → exit",
    ]
    draft = store.load()
    assert draft is not None and draft.routing is not None
    assert draft.routing.default_egress_ref == "exit"
    (route,) = draft.routing.routes
    assert (route.match, route.match_values, route.action, route.target_ref) == (
        "domain",
        ["example.org"],
        action,
        target_ref,
    )


def test_typing_exit_picks_the_role_instead_of_quitting(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [
            ("Which servers should this VPN run on", "all"),
            ("control panel", "1"),
            ("Job for control", "exit"),
            ("Job for exit", "exit"),
            ("Country code", "quit"),
        ]
    )

    assert _wizard(service, shelf, io).run() == "saved"
    assert io.answers == []


def test_domain_adds_cdn_websocket_and_every_path_gets_a_random_path(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [
            *_TWO_SERVER_JOURNEY[:10],
            ("Domain pointed at 198.51.100.20", "vpn.example.org"),
            ("Names of the people", "save"),
        ]
    )

    assert _wizard(service, shelf, io).run() == "saved"

    draft = store.load()
    assert draft is not None and draft.paths is not None
    paths = {path.protocol: path for path in draft.paths.exits[0].paths}
    assert list(paths) == ["reality", "xhttp", "wss", "hysteria2"]
    assert paths["xhttp"].path != paths["wss"].path
    assert all(len(paths[name].path) == 16 and "exit" not in paths[name].path for name in ("xhttp", "wss"))
    assert any("stall after about 20 KB" in line for line in io.output)


def test_without_a_domain_every_invisible_method_uses_the_server_ip(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [
            *_TWO_SERVER_JOURNEY[:10],
            ("Domain pointed at 198.51.100.20", ""),
            ("Names of the people", "save"),
        ]
    )

    assert _wizard(service, shelf, io).run() == "saved"

    draft = store.load()
    assert draft is not None and draft.paths is not None
    paths = draft.paths.exits[0].paths
    assert [path.protocol for path in paths] == ["reality", "xhttp", "hysteria2"]
    assert {path.tls_sni for path in paths[1:]} == {"198.51.100.20"}
    assert not any("stall after about 20 KB" in line for line in io.output)


def test_language_step_comes_first_and_switches_the_following_prompts(tmp_path) -> None:
    from meridian import i18n

    store, shelf, service = _services(tmp_path)
    chosen: list[str] = []
    io = _ScriptedIO([("Language / Язык", "2"), ("", "quit")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
        ask_language=True,
        on_language=lambda code: (chosen.append(code), i18n.set_locale(i18n.normalize_locale(code) or "en")),
    )
    try:
        assert wizard.run() == "saved"
        assert chosen == ["ru"]
        assert io.labels[1] == i18n.catalog("ru")["servers.pick"]
    finally:
        i18n.set_locale("en")


def test_setup_without_a_terminal_refuses_instead_of_taking_defaults(tmp_home, monkeypatch) -> None:
    import typer

    from meridian.commands import setup_v4

    monkeypatch.setattr(setup_v4, "has_tty", lambda: False)
    with pytest.raises(typer.Exit) as exc_info:
        setup_v4.run()
    assert exc_info.value.exit_code == 2


@pytest.mark.parametrize(
    ("sites", "expected"),
    [
        (CamouflageSites(["shop.example.net"], ["dl.google.com"]), "shop.example.net"),
        (CamouflageSites([], ["dl.google.com"]), "dl.google.com"),
        (CamouflageSites([], []), "www.google.com"),
    ],
    ids=["nearby-site-wins", "reachable-known-site", "nothing-reachable"],
)
def test_camouflage_default_prefers_the_exit_network_then_what_it_reaches(tmp_path, sites, expected) -> None:
    store, shelf, service = _services(tmp_path)
    probed: list[str] = []
    io = _ScriptedIO([*_TWO_SERVER_JOURNEY[:11], ("Names of the people", "save")])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
        suggest_sni=lambda ref: (probed.append(ref), sites)[1],
    )

    assert wizard.run() == "saved"

    draft = store.load()
    assert draft is not None and draft.paths is not None
    assert draft.paths.exits[0].paths[0].reality_sni == expected
    assert probed == ["srv-exit"]
    warned = any("could not reach the usual camouflage sites" in line for line in io.output)
    assert warned is (not sites.nearby and not sites.reachable)


def test_new_remote_server_defaults_to_an_unused_name_idea(tmp_path) -> None:
    from meridian.servers import SERVER_NAME_IDEAS

    registry = ServerRegistry(tmp_path / "servers.json")
    shelf = ServerShelf(registry)
    service = SetupDraftService(SetupDraftStore(tmp_path / "setup.json"), shelf)
    registered: list[str] = []
    io = _ScriptedIO([("Server address", "198.51.100.31"), ("Short name", "")])
    wizard = _wizard(service, shelf, io, lambda _address, name, _user, _port: registered.append(name))

    assert wizard._add_server(["astra"])

    [name] = registered
    assert name in SERVER_NAME_IDEAS and name != "astra"
    [ideas] = [line for line in io.output if "Name ideas:" in line]
    assert name in ideas and "astra" not in ideas


class _PickerIO(_ScriptedIO):
    """A terminal with pickers: lists answer from ``picks``, text fields from ``answers``."""

    def __init__(self, answers: Sequence[tuple[str, str]], picks: Sequence[tuple[str, object]]) -> None:
        super().__init__(answers)
        self.picks = list(picks)
        self.calls: list[tuple[str, list[str], str]] = []

    def text(self, label: str, *, default: str = "", validate=None) -> str:
        value = self.ask(label, default=default)
        if validate is not None:
            validate(value)
        return value

    def _pick(self, label: str) -> object:
        fragment, answer = self.picks.pop(0)
        assert fragment in label, f"Expected a picker containing {fragment!r}, got {label!r}"
        return answer

    def select(self, label, choices, *, default="", other="", validate_other=None, details="") -> str:
        self.calls.append(("select", [choice.value for choice in choices], default))
        answer = str(self._pick(label)) or default
        if answer not in [choice.value for choice in choices] and validate_other is not None:
            validate_other(answer)
        return answer

    def multiselect(self, label, choices, *, checked=(), actions=(), details="") -> list[str]:
        self.calls.append(("multiselect", [choice.value for choice in choices], ",".join(checked)))
        return cast(list[str], self._pick(label))


def test_terminal_pickers_check_servers_and_offer_probed_and_typed_camouflage_sites(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _PickerIO(
        [
            ("Country code", ""),
            ("WARP", ""),
            ("Refuse Russian sites", ""),
            ("Domain for the control panel", ""),
            ("Name of this VPN", ""),
            ("Domain pointed at", ""),
            ("Names of the people", "save"),
        ],
        [
            ("Which servers", ["srv-control", "srv-exit"]),
            ("control panel", "srv-control"),
            ("Job for control", "control"),
            ("Job for exit", ""),
            ("Camouflage site", "www.example.org"),
        ],
    )
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
        suggest_sni=lambda _ref: CamouflageSites(["shop.example.net"], ["dl.google.com"]),
    )

    assert wizard.run() == "saved"

    assert io.calls[0] == ("multiselect", ["srv-control", "srv-exit"], "srv-control,srv-exit")
    assert io.calls[-1] == (
        "select",
        [
            "shop.example.net",
            "dl.google.com",
            *(sni for sni in REALITY_SNI_CANDIDATES if sni != "dl.google.com"),
            OWN_DOMAIN,
        ],
        "shop.example.net",
    )
    draft = store.load()
    assert draft is not None and draft.paths is not None
    assert draft.paths.exits[0].paths[0].reality_sni == "www.example.org"


def test_own_domain_choice_asks_for_it_and_marks_the_path_self_steal(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _PickerIO(
        [
            ("Country code", ""),
            ("WARP", ""),
            ("Refuse Russian sites", ""),
            ("Domain for the control panel", ""),
            ("Name of this VPN", ""),
            ("Your domain pointed at", "www.example.org"),
            ("Domain pointed at", ""),
            ("Names of the people", "save"),
        ],
        [
            ("Which servers", ["srv-control", "srv-exit"]),
            ("control panel", "srv-control"),
            ("Job for control", "control"),
            ("Job for exit", ""),
            ("Camouflage site", OWN_DOMAIN),
        ],
    )
    checked: list[tuple[str, str]] = []

    def check(server_ref: str, hostname: str) -> OwnHostname:
        checked.append((server_ref, hostname))
        return OwnHostname("elsewhere", ("203.0.113.5",))

    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
        check_own_domain=check,
    )

    assert wizard.run() == "saved"

    assert checked == [("srv-exit", "www.example.org")]
    assert any("203.0.113.5" in line for line in io.output)
    draft = store.load()
    assert draft is not None and draft.paths is not None
    reality = draft.paths.exits[0].paths[0]
    assert (reality.reality_sni, reality.self_steal) == ("www.example.org", True)


def test_own_domain_refuses_an_ip_address() -> None:
    with pytest.raises(ValueError, match="not an IP"):
        _own_hostname_value("198.51.100.11")


class _FailingReview(_Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.reviews = 0

    def review(self, draft: SetupDraft) -> SetupReview:
        self.reviews += 1
        raise ValueError("Compiled resource logical IDs must be lowercase stable identifiers.")


def test_a_failing_automatic_stage_asks_what_next_instead_of_looping(tmp_path) -> None:
    _store, shelf, service = _services(tmp_path)
    runtime = _FailingReview()
    io = _ScriptedIO(
        [*_TWO_SERVER_JOURNEY, ("What next?", "1"), ("Names of the people", "save")]  # 1 = back to users
    )
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, runtime),
        register_server=lambda *_args: None,
        io=io,
    )

    assert wizard.run() == "saved"
    assert runtime.reviews == 1
    assert sum("lowercase stable identifiers" in line for line in io.output) == 1


def test_auto_approve_stops_on_a_failing_stage(tmp_path) -> None:
    from meridian.core.errors import LocalStateError

    store, shelf, service = _services(tmp_path)
    store.save(SetupDraft.from_intent(single_exit_intent()))
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _FailingReview()),
        register_server=lambda *_args: None,
        io=_ScriptedIO([]),
        auto_approve=True,
    )

    with pytest.raises(LocalStateError, match="lowercase stable identifiers"):
        wizard.run()


def test_names_that_differ_only_by_case_are_reasked(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [*_TWO_SERVER_JOURNEY[:-1], ("Names of the people", "Biba, biba"), ("Names of the people", "save")]
    )

    assert _wizard(service, shelf, io, lambda *_args: None).run() == "saved"
    assert any("even ignoring capital letters" in line for line in io.output)


def test_names_in_any_script_keep_their_spelling_and_get_latin_panel_usernames(tmp_path) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(
        [*_TWO_SERVER_JOURNEY[:-1], ("Names of the people", "Биба, Boba, 王小明"), ("Set this up now?", "no")]
    )

    assert _wizard(service, shelf, io).run() == "saved"

    draft = store.load()
    assert draft is not None and draft.access is not None
    access = draft.access.access
    assert access.users == ["biba", "Boba", "wangxiaoming"]
    assert access.display_names == {"biba": "Биба", "wangxiaoming": "王小明"}
    assert any("Биба → biba" in line for line in io.output)
    assert any("Биба, Boba, 王小明" in line for line in io.output)  # the review shows the typed names


def test_going_back_to_users_keeps_existing_usernames() -> None:
    from meridian.commands.setup_wizard import _access_intent

    previous = _access_intent(["Биба", "Biba"], None)
    assert previous.users == ["biba-2", "Biba"]  # a name the panel accepts claims itself first

    again = _access_intent(["Biba", "Ли", "Биба"], previous)

    assert again.users == ["Biba", "user-li", "biba-2"]
    assert again.display_names == {"user-li": "Ли", "biba-2": "Биба"}
