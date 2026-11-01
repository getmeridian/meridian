"""Indexed SQL selection with one predicate set and authenticated keyset cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from meridian.control.errors import ServiceError
from meridian.control.people_intent import PeopleIntent, read_people_intent
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.control.users import USER_SELECT, user_record
from meridian.core.agent_types import canonical_sha256
from meridian.core.attention import AttentionThresholds, person_reasons
from meridian.core.people_query import PeopleFacets, PeoplePage, PeoplePerson, PeopleQuery
from meridian.core.traffic import period_usage, quota_percent

REASONS = ("expiring", "quota", "unused", "stopped")
LIFECYCLES = ("pending", "renewing", "fixed", "unlimited")
CURSOR_SECONDS = 900


@dataclass
class AttentionEvidence:
    expire_at: str | None
    used_traffic_bytes: int
    traffic_limit_bytes: int
    created_at: str
    first_traffic_at: str | None
    online_at: str | None
    lifetime_traffic_bytes: int


def attention_mask(*values: Any) -> int:
    *evidence, moment = values
    reasons = person_reasons(AttentionEvidence(*evidence), AttentionThresholds(), datetime.fromisoformat(moment))
    return sum(1 << REASONS.index(reason) for reason in reasons)


FACTS = """
WITH evidence AS MATERIALIZED (
 SELECT u.username, u.user_id, u.status, u.tag, u.expire_at, u.created_at,
 u.traffic_limit_bytes, u.activation_duration_days, u.renewal_interval_days, u.renewal_interval_months,
 v.sequence, v.version,
 people_display(u.username) AS display_name,
 people_fold(people_display(u.username)) AS name_key,
 people_used(COALESCE(t.uplink_bytes, 0), COALESCE(t.downlink_bytes, 0),
    u.traffic_upload_offset_bytes, u.traffic_download_offset_bytes,
    u.traffic_period_carried_bytes) AS used,
 COALESCE(t.uplink_bytes + t.downlink_bytes, 0) + u.traffic_carried_bytes AS lifetime,
 t.first_traffic_at AS first_at, t.last_seen_at AS last_at,
 people_time(u.expire_at) AS expiry_key, people_time(t.last_seen_at) AS activity_key
 FROM users u JOIN people_versions v ON v.user_id = u.user_id
 LEFT JOIN traffic_totals t ON t.user_id = u.user_id
 WHERE v.sequence <= :snapshot
), facts AS MATERIALIZED (
 SELECT *, people_quota(used, traffic_limit_bytes) AS share,
 people_attention(expire_at, used, traffic_limit_bytes, created_at, first_at, last_at, lifetime, :now) AS reasons,
 CASE WHEN first_at IS NULL AND last_at IS NULL AND lifetime = 0 THEN 1 ELSE 0 END AS never_used,
 CASE WHEN expire_at IS NULL AND activation_duration_days IS NOT NULL AND first_at IS NULL THEN 'pending'
      WHEN renewal_interval_days IS NOT NULL OR renewal_interval_months IS NOT NULL THEN 'renewing'
      WHEN expire_at IS NOT NULL THEN 'fixed' ELSE 'unlimited' END AS lifecycle
 FROM evidence
)
"""


class PeopleQueryEngine:
    def __init__(self, store: Store, *, clock: Callable[[], float]) -> None:
        self.store, self.clock = store, clock
        self._key = secrets.token_bytes(32)
        self._names: dict[str, str] = {}
        store.db.create_function("people_fold", 1, str.casefold, deterministic=True)
        store.db.create_function("people_display", 1, lambda name: self._names.get(name, name))
        store.db.create_function(
            "people_time",
            1,
            lambda value: datetime.fromisoformat(value).timestamp() if value else None,
            deterministic=True,
        )
        store.db.create_function(
            "people_used",
            5,
            lambda up, down, up_offset, down_offset, carried: (
                period_usage(
                    up, down, upload_offset=up_offset, download_offset=down_offset, carried_bytes=carried
                ).total_bytes
            ),
            deterministic=True,
        )
        store.db.create_function("people_quota", 2, quota_percent, deterministic=True)
        store.db.create_function("people_attention", 8, attention_mask, deterministic=True)

    def intent(self) -> PeopleIntent:
        return read_people_intent(self.store)

    def row_version(self, username: str) -> tuple[str, int] | None:
        row = self.store.row(
            "SELECT u.user_id, v.version FROM users u "
            "JOIN people_versions v ON v.user_id = u.user_id WHERE u.username = ?",
            (username,),
        )
        return (str(row[0]), int(row[1])) if row else None

    def _view(self, query: PeopleQuery) -> tuple[dict[str, Any], list[Any] | None]:
        snapshot = self.store.db.execute("SELECT COALESCE(MAX(sequence), 0) FROM people_versions").fetchone()[0]
        data: dict[str, Any] = {"snapshot": snapshot, "now": format_timestamp(self.clock())}
        last = None
        if query.cursor:
            try:
                payload, signature = query.cursor.split(".")
                expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError
                held = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                if held["query"] != canonical_sha256(query.selection_data()):
                    raise ValueError
                if not 0 <= self.clock() - datetime.fromisoformat(held["now"]).timestamp() <= CURSOR_SECONDS:
                    raise ValueError
                data.update(snapshot=held["snapshot"], now=held["now"])
                last = held["last"]
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise ServiceError(
                    "invalid_request", "The people cursor is invalid or expired; start the query again.", 400
                ) from exc
        return data, last

    @staticmethod
    def predicates(query: PeopleQuery, data: dict[str, Any]) -> dict[str, str]:
        """Both paging and all-matching selection call this exact set."""
        moment = datetime.fromisoformat(data["now"])
        data.update(
            q=query.q.casefold(),
            status=query.status,
            tag="" if query.tag == "none" else query.tag,
            end=(moment + timedelta(days=query.expires or 7)).timestamp(),
            start=moment.timestamp(),
            quota=query.quota or 80,
            lifecycle=query.lifecycle,
        )
        return {
            "q": "(instr(people_fold(username), :q) > 0 OR instr(people_fold(display_name), :q) > 0)"
            if query.q
            else "1",
            "status": "status = :status" if query.status else "1",
            "tag": "tag = :tag" if query.tag else "1",
            "expires": "expiry_key BETWEEN :start AND :end" if query.expires else "1",
            "quota": "share >= :quota" if query.quota else "1",
            "never": "never_used = 1" if query.never else "1",
            "attention": (
                "reasons != 0"
                if query.attention == "all"
                else f"(reasons & {1 << REASONS.index(query.attention)}) != 0"
            )
            if query.attention
            else "1",
            "lifecycle": "lifecycle = :lifecycle" if query.lifecycle else "1",
        }

    @staticmethod
    def _where(predicates: dict[str, str], omit: str = "") -> str:
        return " AND ".join(f"({value})" for key, value in predicates.items() if key != omit)

    @staticmethod
    def _sort(query: PeopleQuery) -> tuple[str, str]:
        return {"name": "name_key", "usage": "used", "expiry": "expiry_key", "activity": "activity_key"}[
            query.sort
        ], "DESC" if query.order == "desc" else "ASC"

    def select(self, query: PeopleQuery, *, now: str | None = None) -> list[str]:
        """Resolve everyone matching at preview time; no paging limit or cursor."""
        query = PeopleQuery.model_validate(query.model_dump())
        if query.cursor:
            raise ServiceError("invalid_request", "Bulk selection needs a query without a cursor.", 400)
        with self.store.lock:
            self._names = self.intent().names
            data, _ = self._view(query)
            if now is not None:
                data["now"] = now
            predicates = self.predicates(query, data)
            key, direction = self._sort(query)
            rows = self.store.db.execute(
                FACTS + f"SELECT username FROM facts WHERE {self._where(predicates)} "
                f"ORDER BY ({key} IS NULL), {key} {direction}, username",
                data,
            ).fetchall()
            return [str(row[0]) for row in rows]

    def _facets(self, predicates: dict[str, str], data: dict[str, Any]) -> tuple[int, int, PeopleFacets]:
        counts = [
            f"SELECT 'total', '', COUNT(*) FROM facts WHERE {self._where(predicates)}",
            "SELECT 'population', '', COUNT(*) FROM facts",
        ]
        for facet in ("status", "tag", "lifecycle"):
            counts.append(
                f"SELECT '{facet}', {facet}, SUM(CASE WHEN {self._where(predicates, facet)} "
                f"THEN 1 ELSE 0 END) FROM facts GROUP BY {facet}"
            )
        for reason, bit in [("", 0), ("all", 0), *[(name, 1 << index) for index, name in enumerate(REASONS)]]:
            check = "1" if reason == "" else "reasons != 0" if reason == "all" else f"(reasons & {bit}) != 0"
            counts.append(
                f"SELECT 'attention', '{reason}', COUNT(*) FROM facts "
                f"WHERE {self._where(predicates, 'attention')} AND ({check})"
            )
        for facet, check in (
            ("expires", "expiry_key BETWEEN :start AND :end"),
            ("quota", "share >= :quota"),
            ("never", "never_used = 1"),
        ):
            counts.append(
                f"SELECT '{facet}', '', COUNT(*) FROM facts WHERE {self._where(predicates, facet)} AND ({check})"
            )
        rows = self.store.db.execute(FACTS + " UNION ALL ".join(counts), data).fetchall()
        facets: dict[str, Any] = {name: {} for name in ("status", "tag", "attention", "lifecycle")}
        total = population = 0
        for facet, value, count in rows:
            if facet == "total":
                total = count
            elif facet == "population":
                population = count
            elif facet in ("expires", "quota", "never"):
                facets[facet] = count
            else:
                facets[facet][("none" if value == "" else value) if facet == "tag" else value] = count
        for name in ("active", "disabled", "expired", "limited"):
            facets["status"].setdefault(name, 0)
        for name in LIFECYCLES:
            facets["lifecycle"].setdefault(name, 0)
        facets["status"][""] = sum(facets["status"].values())
        facets["tag"][""] = sum(facets["tag"].values())
        facets["lifecycle"][""] = sum(facets["lifecycle"].values())
        return total, population, PeopleFacets(**facets)

    def _people(self, names: list[str], intent: PeopleIntent, now: str) -> list[PeoplePerson]:
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        records = {
            row[0]: user_record(row, now=datetime.fromisoformat(now))
            for row in self.store.db.execute(USER_SELECT + f" WHERE u.username IN ({placeholders})", names)
        }
        versions = {
            row[0]: row[1]
            for row in self.store.db.execute(
                f"SELECT u.username, v.version FROM users u JOIN people_versions v ON u.user_id = v.user_id "
                f"WHERE u.username IN ({placeholders})",
                names,
            )
        }
        return [
            PeoplePerson(
                **records[name].model_dump(
                    include=set(PeoplePerson.model_fields) - {"display_name", "row_version", "declared"}
                ),
                display_name=intent.names.get(name, name),
                row_version=versions[name],
                declared=name in intent.declared,
            )
            for name in names
        ]

    def query(self, query: PeopleQuery) -> PeoplePage:
        query = PeopleQuery.model_validate(query.model_dump())
        with self.store.lock:
            intent = self.intent()
            self._names = intent.names
            data, last = self._view(query)
            predicates = self.predicates(query, data)
            total, population, facets = self._facets(predicates, data)
            key, direction = self._sort(query)
            missing = f"({key} IS NULL)"
            fallback = "''" if query.sort == "name" else "0"
            value = f"COALESCE({key}, {fallback})"
            seek = ""
            if last is not None:
                data.update(last_missing=last[0], last_value=last[1], last_name=last[2])
                op = "<" if direction == "DESC" else ">"
                seek = (
                    f" AND ({missing} > :last_missing OR ({missing} = :last_missing "
                    f"AND ({value} {op} :last_value OR ({value} = :last_value AND username > :last_name))))"
                )
            data["limit"] = query.limit + 1
            selected = self.store.db.execute(
                FACTS + f"SELECT username, {missing}, {value} FROM facts WHERE {self._where(predicates)}{seek} "
                f"ORDER BY {missing}, {key} {direction}, username LIMIT :limit",
                data,
            ).fetchall()
            page = selected[: query.limit]
            users = self._people([row[0] for row in page], intent, data["now"])
            cursor = None
            if len(selected) > query.limit:
                held = {
                    "query": canonical_sha256(query.selection_data()),
                    "snapshot": data["snapshot"],
                    "now": data["now"],
                    "last": list(page[-1][1:]) + [page[-1][0]],
                }
                payload = (
                    base64.urlsafe_b64encode(json.dumps(held, separators=(",", ":")).encode()).decode().rstrip("=")
                )
                cursor = payload + "." + hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
            return PeoplePage(
                users=users,
                total=total,
                population=population,
                facets=facets,
                next_cursor=cursor,
                observed_at=data["now"],
            )
