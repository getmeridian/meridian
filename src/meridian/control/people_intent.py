"""Applied topology projection for fleet views; revisions never determine person ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from meridian.control.store import Store


@dataclass
class PeopleIntent:
    revision: int | None = None
    intent: dict[str, Any] = field(default_factory=dict)


def read_people_intent(store: Store) -> PeopleIntent:
    """Read the latest applied topology while ignoring unfinished revisions."""
    result = PeopleIntent()
    rows = store.rows("SELECT number, status, intent FROM revisions WHERE status != 'proposed' ORDER BY number DESC")
    for row in rows:
        intent = json.loads(row["intent"])
        if row["status"] == "applied":
            result.revision, result.intent = row["number"], intent
            break
    return result
