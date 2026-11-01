"""Real service pages for console triage scenarios, without a second predicate."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from meridian.control.models import RevisionProposal, RevisionResult, UserImport, UserImportBatch, UserUpdate
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from meridian.core.people_query import PeopleQuery
from tests.support.control_service import FLEET_ID

NOW = "2026-11-01T12:00:00Z"


def valid_options(values: dict) -> dict:
    result = {}
    enums = {
        "status": {"active", "disabled", "expired", "limited"},
        "attention": {"all", "unused", "stopped", "expiring", "quota"},
        "lifecycle": {"pending", "renewing", "fixed", "unlimited"},
        "sort": {"name", "usage", "expiry", "activity"},
        "order": {"asc", "desc"},
    }
    for name, value in values.items():
        if name in enums and value in enums[name]:
            result[name] = value
        elif name == "q":
            result[name] = str(value)[:256]
        elif name == "tag" and (value == "none" or re.fullmatch(r"[A-Z0-9_]{1,16}", str(value))):
            result[name] = value
        elif name == "never":
            result[name] = value is True or value == "1"
        elif name in ("expires", "quota") and re.fullmatch(r"[0-9]{1,3}", str(value)):
            number = int(value)
            if 1 <= number <= (365 if name == "expires" else 100):
                result[name] = number
    return result


def with_people_replies(data: dict) -> dict:
    """Return the actual initial and edited queries' pages to the DOM boundary."""
    values = {
        name: entries[0]
        for name, entries in parse_qs(data.get("hash", "").partition("?")[2]).items()
        if len(entries) == 1
    }
    initial = valid_options(values)
    final = {**initial, **valid_options(data.get("change", {}))}
    if "sort" in data.get("change", {}) and "order" not in data["change"]:
        final["order"] = "desc" if final["sort"] in ("usage", "activity") else "asc"
    with (
        tempfile.TemporaryDirectory(prefix="meridian-people-dom-") as temp,
        patch("meridian.control.service.utc_now", lambda: NOW),
    ):
        service = ControlService(
            Path(temp) / "control.db",
            fleet_id=FLEET_ID,
            version="4.0.0",
            clock=lambda: datetime.fromisoformat(NOW).timestamp(),
        )
        try:
            rows = data["people"]
            imports = []
            for index, row in enumerate(rows):
                used = row.get("used_traffic_bytes", 0)
                imports.append(
                    UserImport(
                        username=row["username"],
                        uuid=f"00000000-0000-4000-8000-{index:012d}",
                        sub_token=f"sample-token-{index:010d}",
                        status=row.get("status", "active"),
                        tag=row.get("tag", ""),
                        expire_at=row.get("expire_at"),
                        used_traffic_bytes=used,
                        lifetime_traffic_bytes=used,
                        traffic_limit_bytes=min(row.get("traffic_limit_bytes", 0), 2**53 - 1),
                        created_at="2026-10-01T12:00:00Z" if "unused" in row.get("attention_reasons", []) else NOW,
                    )
                )
            for start in range(0, len(imports), 10000):
                service.import_users(UserImportBatch(users=imports[start : start + 10000]))
            for row in rows:
                user = service.get_user(row["username"])
                assert user is not None
                if row.get("traffic_limit_bytes", 0) > 2**53 - 1:
                    service.update_user(user.username, UserUpdate(traffic_limit_bytes=row["traffic_limit_bytes"]))
                if row.get("online_at"):
                    service._db.execute(
                        "INSERT INTO traffic_totals (user_id, uplink_bytes, downlink_bytes, last_seen_at, "
                        "first_traffic_at, last_traffic_node) VALUES (?, 0, 0, ?, ?, NULL)",
                        (user.user_id, row["online_at"], row["online_at"]),
                    )
            if data.get("names"):
                revision = service.revisions.propose(
                    RevisionProposal(
                        intent={"access": {"users": [], "display_names": data["names"]}},
                        plan_hash="a" * 64,
                        review_hash="b" * 64,
                    ),
                    caller=ROOT,
                )
                service.revisions.approve(revision.number, caller=ROOT)
                service.revisions.record_result(revision.number, RevisionResult(status="applied"))
            replies = {}
            for options in (initial, final):
                query = PeopleQuery(**options)
                key = json.dumps(query.selection_data(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                replies[key] = service.people.query(query).model_dump(mode="json")
            return {**data, "replies": replies}
        finally:
            service.close()
