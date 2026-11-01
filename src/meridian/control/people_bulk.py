"""Read-only frozen reviews and atomic bulk commits with a short, durable undo."""

from __future__ import annotations

import hmac
import json
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from meridian.control.account_lifecycle import enforce_user_schedules
from meridian.control.bulk_effects import BulkEffect, prepare_effect, project_effect
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import NodeSpec, UserRecord
from meridian.control.node_documents import with_users
from meridian.control.people_query import PeopleQueryEngine
from meridian.control.period_usage import reset_period
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.control.traffic import TrafficLedger
from meridian.control.user_updates import save_user_fields
from meridian.core.agent_desired import MAX_USERS_PER_INBOUND
from meridian.core.agent_types import canonical_sha256
from meridian.core.fleet_events import BulkEventDetail
from meridian.core.people_bulk import (
    BULK_WRITABLE_FIELDS,
    BulkCommitRequest,
    BulkCounts,
    BulkFields,
    BulkOutcome,
    BulkPersonChange,
    BulkPreview,
    BulkPreviewRequest,
    BulkReceipt,
    BulkWarning,
)

PREVIEW_SECONDS = 600
UNDO_SECONDS = 600
MAX_PREVIEWS = 16
MAX_PREVIEW_PEOPLE = 100000


@dataclass
class FrozenPreview:
    review: BulkPreview
    versions: dict[str, tuple[str, int]]
    effects: dict[str, BulkEffect]
    nodes: str


def changed_fields(before: BulkFields, after: BulkFields | None) -> list[str]:
    return (
        ["delete"]
        if after is None
        else [name for name in BulkFields.model_fields if getattr(before, name) != getattr(after, name)]
    )


def counts(people: list[BulkPersonChange]) -> BulkCounts:
    return BulkCounts(
        targeted=len(people),
        changed=sum(person.outcome == "changed" for person in people),
        unchanged=sum(person.outcome == "unchanged" for person in people),
        cli_required=sum(person.outcome == "cli_required" for person in people),
    )


class BulkLedger:
    def __init__(
        self,
        store: Store,
        people: PeopleQueryEngine,
        events: EventLedger,
        traffic: TrafficLedger,
        *,
        read_user: Callable[[str], UserRecord],
        read_users: Callable[[], list[UserRecord]],
        compose: Callable[[], None],
        clock: Callable[[], float],
    ) -> None:
        self.store, self.people, self.events, self.traffic = store, people, events, traffic
        self.read_user, self.read_users, self.compose, self.clock = read_user, read_users, compose, clock
        self._previews: OrderedDict[str, FrozenPreview] = OrderedDict()

    def _nodes(self) -> str:
        return canonical_sha256(
            [
                tuple(row)
                for row in self.store.rows(
                    "SELECT node_id, spec_sha256 FROM nodes WHERE spec IS NOT NULL ORDER BY node_id"
                )
            ]
        )

    def _changed_nodes(self, effects: dict[str, BulkEffect], deleted: set[str]) -> int:
        before = self.read_users()
        after = [
            effects[user.username].user if user.username in effects else user
            for user in before
            if user.username not in deleted
        ]
        specs = [
            NodeSpec.model_validate_json(row[0])
            for row in self.store.rows("SELECT spec FROM nodes WHERE spec IS NOT NULL")
        ]
        if (
            any(spec.managed_inbounds for spec in specs)
            and sum(user.status == "active" for user in after) > MAX_USERS_PER_INBOUND
        ):
            raise ServiceError(
                "invalid_request",
                f"This change would exceed {MAX_USERS_PER_INBOUND} active people per managed inbound. "
                "Select fewer people.",
                400,
            )
        return sum(
            canonical_sha256(with_users(spec, before)) != canonical_sha256(with_users(spec, after)) for spec in specs
        )

    def preview(self, request: BulkPreviewRequest) -> BulkPreview:
        request = BulkPreviewRequest.model_validate(request.model_dump(mode="json", exclude_unset=True))
        with self.store.lock:
            now = format_timestamp(self.clock())
            if request.usernames is not None:
                names = request.usernames
            else:
                assert request.query is not None
                names = self.people.select(request.query, now=now)
            if len(names) > 50000:
                raise ServiceError("invalid_request", "Select at most 50000 people per operation.", 400)
            intent = self.people.intent()
            versions: dict[str, tuple[str, int]] = {}
            effects: dict[str, BulkEffect] = {}
            changes: list[BulkPersonChange] = []
            deleted: set[str] = set()
            reversible = request.action.action not in ("delete", "reset-traffic")
            for name in names:
                user = self.read_user(name)
                version = self.people.row_version(name)
                assert version is not None
                versions[name] = version
                before = BulkFields.from_user(user)
                warnings: list[BulkWarning] = []
                command = None
                if request.action.action == "delete":
                    after = None
                    if name in intent.declared:
                        command = f"meridian client remove {name}"
                        warnings.append("topology_delete")
                        after = before
                    else:
                        deleted.add(name)
                else:
                    effect = prepare_effect(user, request.action, now)
                    effects[name] = effect
                    after = BulkFields.from_user(effect.user)
                    if effect.reset:
                        reversible = False
                        warnings.append("period_reset")
                    if user.status == "disabled" and effect.user.status == "disabled":
                        warnings.append("disabled")
                    if request.action.action == "enable" and effect.user.status in ("expired", "limited"):
                        warnings.append(
                            "expiry_blocks_enable" if effect.user.status == "expired" else "quota_blocks_enable"
                        )
                fields = changed_fields(before, after)
                outcome: BulkOutcome = "cli_required" if command else "changed" if fields else "unchanged"
                changes.append(
                    BulkPersonChange(
                        username=name,
                        display_name=intent.names.get(name, name),
                        row_version=version[1],
                        before=before,
                        after=after,
                        changes=fields,
                        outcome=outcome,
                        cli_command=command,
                        warnings=warnings,
                    )
                )
            warnings = []
            if not reversible:
                warnings.append("irreversible")
            if not any(person.outcome == "changed" for person in changes):
                warnings.append("no_change")
            unsigned = BulkPreview(
                id=secrets.token_hex(16),
                preview_hash="0" * 64,
                created_at=now,
                expires_at=format_timestamp(self.clock() + PREVIEW_SECONDS),
                action=request.action,
                people=changes,
                counts=counts(changes),
                changed_nodes=self._changed_nodes(effects, deleted),
                reversible=reversible,
                warnings=warnings,
            )
            review = BulkPreview.model_validate(
                {
                    **unsigned.model_dump(mode="json"),
                    "preview_hash": canonical_sha256(unsigned.model_dump(mode="json", exclude={"preview_hash"})),
                }
            )
            frozen = FrozenPreview(review, versions, effects, self._nodes())
            # Bounded process memory; previews never write SQLite, snapshots, events, or logs.
            expired = [
                key
                for key, item in self._previews.items()
                if datetime.fromisoformat(item.review.expires_at) <= datetime.fromisoformat(now)
            ]
            for key in expired:
                self._previews.pop(key)
            self._previews[review.id] = frozen
            while (
                len(self._previews) > MAX_PREVIEWS
                or sum(len(item.versions) for item in self._previews.values()) > MAX_PREVIEW_PEOPLE
            ):
                self._previews.popitem(last=False)
            return review

    def receipt(self, receipt_id: str) -> BulkReceipt:
        row = self.store.row("SELECT body FROM bulk_receipts WHERE id = ?", (receipt_id,))
        if row is None:
            raise ServiceError("not_found", "This bulk receipt is unknown.", 404)
        return BulkReceipt.model_validate_json(row[0])

    def _refuse_stale(self, versions: dict[str, tuple[str, int]]) -> None:
        stale = [name for name, expected in versions.items() if self.people.row_version(name) != expected]
        if stale:
            raise ServiceError(
                "stale_people",
                "These people changed since review: " + ", ".join(stale) + ". Preview again.",
                409,
                subjects=stale,
            )

    def commit(self, request: BulkCommitRequest) -> BulkReceipt:
        request = BulkCommitRequest.model_validate(request.model_dump())
        with self.store.transaction():
            existing = self.store.row("SELECT preview_hash FROM bulk_receipts WHERE id = ?", (request.preview_id,))
            if existing:
                if not hmac.compare_digest(existing[0], request.preview_hash):
                    raise ServiceError("preview_mismatch", "The preview hash does not match.", 409)
                return self.receipt(request.preview_id)
            frozen = self._previews.get(request.preview_id)
            if frozen is None:
                raise ServiceError(
                    "preview_expired", "This preview expired or the service restarted. Preview again.", 409
                )
            review = frozen.review
            if not hmac.compare_digest(review.preview_hash, request.preview_hash):
                raise ServiceError("preview_mismatch", "The preview hash does not match.", 409)
            now = format_timestamp(self.clock())
            if datetime.fromisoformat(now) > datetime.fromisoformat(review.expires_at):
                raise ServiceError("preview_expired", "This preview expired. Preview again.", 409)
            self._refuse_stale(frozen.versions)
            if self._nodes() != frozen.nodes:
                raise ServiceError("stale_people", "The managed nodes changed since review. Preview again.", 409)
            declared = self.people.intent().declared
            stale = []
            for person in review.people:
                if review.action.action == "delete":
                    if (person.cli_command is not None) != (person.username in declared):
                        stale.append(person.username)
                elif (
                    BulkFields.from_user(project_effect(frozen.effects[person.username].user, now).user) != person.after
                ):
                    stale.append(person.username)
            if stale:
                raise ServiceError(
                    "stale_people",
                    "These people's enforcement changed since review: " + ", ".join(stale) + ". Preview again.",
                    409,
                    subjects=stale,
                )
            self._changed_nodes(frozen.effects, {person.username for person in review.people if person.after is None})
            outcomes = []
            for person in review.people:
                if person.outcome != "changed":
                    outcomes.append(person)
                    continue
                user = self.read_user(person.username)
                effect = frozen.effects.get(person.username)
                if effect is None:
                    self.traffic.forget_user(user.user_id)
                    self.store.db.execute("DELETE FROM users WHERE user_id = ?", (user.user_id,))
                    self.events.append("user.removed", user.username, now=now)
                    after = None
                else:
                    requested = effect.requested or effect.user
                    save_user_fields(self.store, requested, BULK_WRITABLE_FIELDS)
                    self.events.user_updated(user, requested, now=now)
                    if review.action.action == "reset-traffic":
                        reset_period(self.store, user, review.created_at)
                        self.events.append("user.traffic_reset", user.username, now=now)
                    enforce_user_schedules(
                        self.store,
                        [self.read_user(user.username)],
                        review.created_at,
                        read_user=self.read_user,
                        reset_traffic=lambda person, moment: reset_period(self.store, person, moment),
                        events=self.events,
                        event_now=now,
                    )
                    actual = self.read_user(user.username)
                    after = BulkFields.from_user(actual)
                    if after != person.after:
                        raise ServiceError(
                            "stale_people", f"{user.username} changed during enforcement. Preview again.", 409
                        )
                outcomes.append(BulkPersonChange.model_validate({**person.model_dump(), "after": after}))
            if review.counts.changed:
                self.compose()
            receipt = BulkReceipt(
                id=review.id,
                preview_hash=review.preview_hash,
                action=review.action,
                committed_at=now,
                counts=review.counts,
                people=outcomes,
                changed_nodes=review.changed_nodes,
                undo_until=format_timestamp(self.clock() + UNDO_SECONDS)
                if review.reversible and review.counts.changed
                else None,
            )
            versions = {
                person.username: self.people.row_version(person.username)
                for person in outcomes
                if person.outcome == "changed" and person.after is not None
            }
            self.store.db.execute(
                "INSERT INTO bulk_receipts VALUES (?, ?, ?, ?)",
                (receipt.id, receipt.preview_hash, receipt.model_dump_json(), json.dumps(versions)),
            )
            self.events.append(
                "people.bulk",
                receipt.id,
                detail=BulkEventDetail(action=review.action.action, **review.counts.model_dump()),
                now=now,
            )
        self._previews.pop(request.preview_id, None)
        return receipt

    def undo(self, receipt_id: str) -> BulkReceipt:
        with self.store.transaction():
            receipt = self.receipt(receipt_id)
            now = format_timestamp(self.clock())
            if receipt.undone_at:
                raise ServiceError("undo_refused", "This operation was already undone.", 409)
            if receipt.undo_until is None:
                raise ServiceError("undo_refused", "This operation cannot be undone.", 409)
            if datetime.fromisoformat(now) > datetime.fromisoformat(receipt.undo_until):
                raise ServiceError("undo_refused", "The undo window expired.", 409)
            row = self.store.row("SELECT versions FROM bulk_receipts WHERE id = ?", (receipt_id,))
            assert row is not None
            versions = {name: (value[0], value[1]) for name, value in json.loads(row[0]).items()}
            self._refuse_stale(versions)
            outcomes = []
            for person in receipt.people:
                if person.outcome != "changed":
                    continue
                user = self.read_user(person.username)
                restored = UserRecord.model_validate(
                    {**user.model_dump(), **{field: getattr(person.before, field) for field in BULK_WRITABLE_FIELDS}}
                )
                effect = project_effect(restored, now)
                if effect.reset:
                    raise ServiceError(
                        "undo_refused",
                        f"{person.username}'s schedule now requires a traffic reset. Preview a new change.",
                        409,
                    )
                if BulkFields.from_user(effect.user) != person.before:
                    raise ServiceError(
                        "stale_people",
                        f"{person.username}'s enforcement changed since review. Preview a new change.",
                        409,
                        subjects=[person.username],
                    )
                save_user_fields(self.store, effect.user, BULK_WRITABLE_FIELDS)
                actual = self.read_user(person.username)
                self.events.user_updated(user, actual, now=now)
                before, after = BulkFields.from_user(user), BulkFields.from_user(actual)
                outcomes.append(
                    BulkPersonChange(
                        username=person.username,
                        display_name=person.display_name,
                        row_version=versions[person.username][1],
                        before=before,
                        after=after,
                        changes=changed_fields(before, after),
                        outcome="changed",
                    )
                )
            self.compose()
            updated = BulkReceipt.model_validate({**receipt.model_dump(), "undone_at": now, "undo_people": outcomes})
            self.store.db.execute(
                "UPDATE bulk_receipts SET body = ? WHERE id = ?", (updated.model_dump_json(), receipt.id)
            )
            self.events.append(
                "people.bulk",
                receipt.id,
                detail=BulkEventDetail(action="undo", **counts(outcomes).model_dump()),
                now=now,
            )
            return updated
