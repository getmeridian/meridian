"""Presentation tests for resumable V4 setup navigation."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

from meridian.commands.setup_wizard import SetupWizard
from meridian.compiler import compile_topology
from meridian.core.setup import SetupDraft
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.persistence import SetupDraftStore
from meridian.setup.runtime import (
    SetupReview,
    SetupRuntime,
    SetupVerification,
)
from meridian.setup.service import SetupDraftService
from meridian.setup.shelf import ServerShelf


class _ScriptedIO:
    def __init__(self, answers: Sequence[str]) -> None:
        self.answers = list(answers)
        self.output: list[str] = []

    def write(self, message: str = "") -> None:
        self.output.append(message)

    def ask(self, label: str, *, default: str = "") -> str:
        assert self.answers, f"Unexpected prompt: {label}"
        answer = self.answers.pop(0)
        return answer or default

    def approve(self, label: str) -> bool:
        raise AssertionError(f"Unexpected direct approval prompt: {label}")


class _Runtime:
    def review(self, draft: SetupDraft) -> SetupReview:
        intent = draft.to_intent()
        return SetupReview(
            intent=intent,
            plan=compile_topology(intent),
        )

    def apply(self, draft: SetupDraft) -> SimpleNamespace:
        return SimpleNamespace(
            all_succeeded=True,
            changed=True,
            failed=[],
            results=[object()],
            plan_hash=draft.review_hash,
        )

    def verify(self, draft: SetupDraft) -> SetupVerification:
        return SetupVerification(
            plan_hash=draft.applied_plan_hash,
            node_count=1,
            subscription_urls={"default": "https://panel.example/api/sub/default"},
        )


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
    io = _ScriptedIO(
        [
            "all",
            "1",
            "2",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "yes",
            "yes",
        ]
    )
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
    assert any("https://panel.example/api/sub/default" in line for line in io.output)


def test_details_repeats_prompt_and_quit_keeps_progress_file_untouched(
    tmp_path,
) -> None:
    store, shelf, service = _services(tmp_path)
    io = _ScriptedIO(["details", "quit"])
    wizard = SetupWizard(
        service=service,
        shelf=shelf,
        runtime=cast(SetupRuntime, _Runtime()),
        register_server=lambda *_args: None,
        io=io,
    )

    assert wizard.run() == "saved"
    assert store.load() is None
    assert any("stable IDs" in line for line in io.output)


def test_back_from_review_invalidates_access_and_review_hash(
    tmp_path,
) -> None:
    store, shelf, service = _services(tmp_path)
    seed_io = _ScriptedIO(
        [
            "all",
            "1",
            "2",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "save",
        ]
    )
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

    io = _ScriptedIO(["back", "save"])
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
