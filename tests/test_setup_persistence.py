"""Atomic setup draft persistence and saved-server shelf flow."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.core.errors import LocalStateCorruptedError, LocalStateError
from meridian.core.setup import (
    SetupAccessSelection,
    SetupDraft,
    SetupExitPaths,
    SetupExitRole,
    SetupPathSelection,
    SetupRoleSelection,
    SetupRoutingSelection,
)
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ProtocolPathIntent
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup import ServerShelf, SetupDraftService, SetupDraftStore


def _store(path: Path) -> SetupDraftStore:
    return SetupDraftStore(path, clock=lambda: datetime(2026, 7, 14, 12, 30, tzinfo=UTC))


def _registry(path: Path) -> ServerRegistry:
    registry = ServerRegistry(path)
    registry.add(
        ServerEntry(
            host="198.51.100.10",
            name="Control",
            id="srv-control",
            auth_state="key_ready",
            last_validated_at="2026-07-14T12:00:00Z",
        )
    )
    registry.add(
        ServerEntry(
            host="198.51.100.20",
            name="Exit",
            id="srv-exit",
            auth_state="validated",
            last_validated_at="2026-07-14T12:05:00Z",
        )
    )
    return registry


def _roles() -> SetupRoleSelection:
    return SetupRoleSelection(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[SetupExitRole(id="exit-a", server_ref="srv-exit", region="DE")],
    )


def _paths() -> SetupPathSelection:
    return SetupPathSelection(
        exits=[
            SetupExitPaths(
                exit_ref="exit-a",
                paths=[
                    ProtocolPathIntent(
                        id="reality-primary",
                        protocol="reality",
                        reality_sni="www.microsoft.com",
                    )
                ],
            )
        ]
    )


class TestSetupDraftStore:
    def test_missing_draft_is_fresh_but_existing_corruption_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "setup.json"
        store = _store(path)

        assert store.load() is None

        path.write_text("")
        with pytest.raises(LocalStateCorruptedError, match="file is empty"):
            store.load()

        path.write_text("{broken")
        with pytest.raises(LocalStateCorruptedError, match="JSON is malformed"):
            store.load()

    def test_save_is_atomic_private_and_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "setup.json"
        store = _store(path)

        saved = store.save(SetupDraft())

        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert saved.updated_at == "2026-07-14T12:30:00Z"
        assert store.load() == saved
        assert json.loads(path.read_text())["schema"] == "meridian.setup/v1"

    def test_failed_replace_preserves_previous_draft(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "setup.json"
        store = _store(path)
        first = store.save(SetupDraft())
        original = path.read_bytes()

        def fail_replace(source: str, destination: Path) -> None:
            del source, destination
            raise OSError("disk unavailable")

        monkeypatch.setattr("meridian.setup.persistence.os.replace", fail_replace)

        with pytest.raises(OSError, match="disk unavailable"):
            store.save(first.model_copy(update={"revision": 1}))

        assert path.read_bytes() == original

    def test_secret_shaped_extra_field_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "setup.json"
        payload = {
            "schema": "meridian.setup/v1",
            "current_stage": "servers",
            "completed_stages": [],
            "password": "must-not-persist",
        }
        path.write_text(json.dumps(payload))

        with pytest.raises(LocalStateCorruptedError, match="Extra inputs are not permitted"):
            _store(path).load()


class TestServerShelf:
    def test_human_selection_becomes_immutable_profile_refs(self, tmp_path: Path) -> None:
        shelf = ServerShelf(_registry(tmp_path / "servers.json"))

        assert shelf.canonical_refs(["Control", "198.51.100.20"]) == ["srv-control", "srv-exit"]

    def test_unvalidated_server_cannot_enter_ready_setup(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path / "servers.json")
        registry.add(ServerEntry(host="198.51.100.30", name="New", id="srv-new"))

        with pytest.raises(LocalStateError, match="has not passed SSH validation"):
            ServerShelf(registry).canonical_refs(["New"])

    def test_cards_show_multiple_roles_without_duplicating_servers(self, tmp_path: Path) -> None:
        shelf = ServerShelf(_registry(tmp_path / "servers.json"))
        roles = SetupRoleSelection(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                SetupExitRole(id="exit-a", server_ref="srv-exit", region="DE"),
                SetupExitRole(id="exit-b", server_ref="srv-control", region="NL"),
            ],
        )

        cards = {card.server_ref: card for card in shelf.items(roles)}

        assert cards["srv-control"].capabilities == ["panel", "exit"]
        assert cards["srv-control"].region == "NL"
        assert cards["srv-exit"].validation_state == "validated"


class TestSetupDraftService:
    def test_every_completed_stage_is_durable_and_resumable(self, tmp_path: Path) -> None:
        store = _store(tmp_path / "setup.json")
        service = SetupDraftService(store, ServerShelf(_registry(tmp_path / "servers.json")))

        assert service.select_servers(["Control", "Exit"]).current_stage == "roles"
        assert store.load().current_stage == "roles"
        assert service.assign_roles(_roles()).current_stage == "paths"
        assert service.configure_paths(_paths()).current_stage == "routing"
        assert service.configure_routing(SetupRoutingSelection(default_egress_ref="exit-a")).current_stage == "access"
        assert (
            service.configure_access(SetupAccessSelection(access=AccessIntent(users=["default"]))).current_stage
            == "review"
        )
        assert service.mark_reviewed("sha256:reviewed").current_stage == "apply"
        assert service.mark_applied("sha256:reviewed").current_stage == "verify"
        assert service.mark_verified("2026-07-14T13:00:00Z").current_stage == "handoff"

        completed = service.complete_handoff()

        assert completed.completed_stages[-1] == "handoff"
        assert store.load() == completed

    def test_back_invalidates_stale_downstream_answers(self, tmp_path: Path) -> None:
        store = _store(tmp_path / "setup.json")
        service = SetupDraftService(store, ServerShelf(_registry(tmp_path / "servers.json")))
        service.select_servers(["Control", "Exit"])
        service.assign_roles(_roles())
        service.configure_paths(_paths())
        service.configure_routing(SetupRoutingSelection(default_egress_ref="exit-a"))
        service.configure_access(SetupAccessSelection(access=AccessIntent(users=["default"])))

        rewound = service.back_to("roles")

        assert rewound.current_stage == "roles"
        assert rewound.completed_stages == ["servers"]
        assert rewound.roles is None
        assert rewound.paths is None
        assert rewound.routing is None
        assert rewound.access is None

    def test_apply_rejects_payload_not_shown_at_review(self, tmp_path: Path) -> None:
        store = _store(tmp_path / "setup.json")
        service = SetupDraftService(store, ServerShelf(_registry(tmp_path / "servers.json")))
        service.select_servers(["Control", "Exit"])
        service.assign_roles(_roles())
        service.configure_paths(_paths())
        service.configure_routing(SetupRoutingSelection(default_egress_ref="exit-a"))
        service.configure_access(SetupAccessSelection(access=AccessIntent(users=["default"])))
        service.mark_reviewed("sha256:reviewed")

        with pytest.raises(LocalStateError, match="does not match the reviewed plan"):
            service.mark_applied("sha256:different")
