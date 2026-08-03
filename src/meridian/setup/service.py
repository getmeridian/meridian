"""Application service for resumable setup progress."""

from __future__ import annotations

from typing import Any

from meridian.core.errors import LocalStateError
from meridian.core.setup import (
    SETUP_STAGE_ORDER,
    SetupAccessSelection,
    SetupDraft,
    SetupPathSelection,
    SetupRoleSelection,
    SetupRoutingSelection,
    SetupStage,
)
from meridian.setup.persistence import SetupDraftStore
from meridian.setup.shelf import ServerShelf


class SetupDraftService:
    """Persist each completed setup stage and invalidate stale downstream data."""

    def __init__(self, store: SetupDraftStore, shelf: ServerShelf) -> None:
        self._store = store
        self._shelf = shelf

    def load_or_create(self) -> SetupDraft:
        """Load resumable progress or return a fresh in-memory draft."""
        return self._store.load() or SetupDraft()

    def select_servers(self, queries: list[str], *, require_ready: bool = True) -> SetupDraft:
        refs = self._shelf.canonical_refs(queries, require_ready=require_ready)
        return self._advance("servers", server_refs=refs)

    def assign_roles(self, roles: SetupRoleSelection) -> SetupDraft:
        draft = self._prepare_stage("roles")
        selected = set(draft.server_refs)
        used = {
            roles.control.server_ref,
            *[exit_.server_ref for exit_ in roles.exits],
            *[server_ref for relay in roles.transparent_relays for server_ref in relay.hop_server_refs],
            *[gateway.server_ref for gateway in roles.routing_gateways],
        }
        missing = sorted(used - selected)
        if missing:
            raise LocalStateError(
                f"Role assignments reference unselected servers: {', '.join(missing)}.",
                hint="Go back to the server shelf and select every server used by a role.",
            )
        return self._complete(draft, roles=roles)

    def configure_paths(self, paths: SetupPathSelection) -> SetupDraft:
        draft = self._prepare_stage("paths")
        if draft.roles is None:
            raise self._stage_error("roles")
        expected = {exit_.id for exit_ in draft.roles.exits}
        actual = {selection.exit_ref for selection in paths.exits}
        if actual != expected:
            raise LocalStateError(
                "Protocol paths must cover every selected exit exactly once.",
                hint="Go back and configure all exit cards before continuing.",
            )
        return self._complete(draft, paths=paths)

    def configure_routing(self, routing: SetupRoutingSelection) -> SetupDraft:
        return self._advance("routing", routing=routing)

    def configure_access(self, access: SetupAccessSelection) -> SetupDraft:
        return self._advance("access", access=access)

    def mark_reviewed(self, review_hash: str) -> SetupDraft:
        if not review_hash.strip():
            raise LocalStateError("The reviewed plan hash cannot be empty.", hint="Compile and review the plan again.")
        draft = self._prepare_stage("review")
        try:
            draft.to_intent()
        except ValueError as exc:
            raise LocalStateError(str(exc), hint="Go back and complete the missing setup choices.") from exc
        return self._complete(draft, review_hash=review_hash)

    def mark_applied(self, plan_hash: str) -> SetupDraft:
        draft = self._prepare_stage("apply")
        if not draft.review_hash or plan_hash != draft.review_hash:
            raise LocalStateError(
                "The apply payload does not match the reviewed plan.",
                hint="Review the newly compiled plan before applying it.",
            )
        return self._complete(draft, applied_plan_hash=plan_hash)

    def mark_verified(self, verified_at: str) -> SetupDraft:
        if not verified_at.strip():
            raise LocalStateError("Verification time cannot be empty.", hint="Run live verification again.")
        return self._advance("verify", verified_at=verified_at)

    def complete_handoff(self) -> SetupDraft:
        return self._advance("handoff")

    def back_to(self, stage: SetupStage) -> SetupDraft:
        """Persist an explicit backward navigation and invalidate dependants."""
        draft = self.load_or_create()
        if SETUP_STAGE_ORDER.index(stage) > SETUP_STAGE_ORDER.index(draft.current_stage):
            raise LocalStateError(
                f"Cannot skip forward from {draft.current_stage} to {stage}.",
                hint="Complete the current setup stage first.",
            )
        return self._store.save(draft.invalidate_from(stage))

    def discard(self) -> bool:
        """Discard progress only after the presentation layer confirms intent."""
        return self._store.delete()

    def _advance(self, stage: SetupStage, **updates: Any) -> SetupDraft:
        return self._complete(self._prepare_stage(stage), **updates)

    def _prepare_stage(self, stage: SetupStage) -> SetupDraft:
        draft = self.load_or_create()
        current_index = SETUP_STAGE_ORDER.index(draft.current_stage)
        stage_index = SETUP_STAGE_ORDER.index(stage)
        if stage_index > current_index:
            raise LocalStateError(
                f"Cannot skip setup stage {draft.current_stage}.",
                hint="Finish the current stage before continuing.",
            )
        if stage_index < current_index or stage in draft.completed_stages:
            return draft.invalidate_from(stage)
        return draft

    def _complete(self, draft: SetupDraft, **updates: Any) -> SetupDraft:
        candidate = SetupDraft.model_validate(
            draft.model_copy(update={"revision": draft.revision + 1, **updates}).model_dump(mode="json", by_alias=True)
        )
        try:
            advanced = candidate.complete_current_stage()
        except ValueError as exc:
            raise LocalStateError(str(exc), hint="Complete this setup stage before continuing.") from exc
        return self._store.save(advanced)

    @staticmethod
    def _stage_error(stage: str) -> LocalStateError:
        return LocalStateError(
            f"Setup {stage} are missing.",
            hint=f"Go back and complete {stage} before continuing.",
        )
