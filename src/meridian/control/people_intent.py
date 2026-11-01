"""People ownership and names from the service's reviewed revisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from meridian.control.store import Store


@dataclass
class PeopleIntent:
    revision: int | None = None
    intent: dict[str, Any] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)


def read_people_intent(store: Store) -> PeopleIntent:
    """Include approved and failed creations until the latest applied declaration."""
    result = PeopleIntent()
    rows = store.rows("SELECT number, status, intent FROM revisions WHERE status != 'proposed' ORDER BY number DESC")
    for row in rows:
        intent = json.loads(row["intent"])
        access = intent.get("access", {})
        result.declared.update(access.get("users", []))
        if row["status"] == "applied":
            result.revision, result.intent = row["number"], intent
            result.names = access.get("display_names", {})
            break
    return result
