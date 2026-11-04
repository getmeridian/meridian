"""Legacy people projection for the frozen website demo, removed with its phase-5 adapter."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import Field

from meridian.core.models import CoreModel
from scripts.showcase_seed import FLEET_ID


class LegacyDemoProposal(CoreModel):
    schema_version: Literal["meridian.people-removal/v1"] = Field(default="meridian.people-removal/v1", alias="schema")
    fleet_id: str
    revision: int
    generation: int
    plan_hash: str
    intent_sha256: str
    usernames: list[str]


def legacy_demo_responses(responses: dict[str, Any]) -> dict[str, Any]:
    """Project already-validated current recordings into the unchanged demo's old data shape."""
    result = deepcopy(responses)
    names = ["Tavi", "Neri"]
    result["GET ui/api/fleet"]["declared_users"] = names
    for key, body in result.items():
        if key.startswith("GET ui/api/people/") and key.endswith("/share"):
            body["declared"] = body["username"] in names
        if key.startswith("GET api/people") and "users" in body:
            for person in body["users"]:
                person["declared"] = person["username"] in names
    history = result.get("GET api/revisions", {}).get("revisions", [])
    revision: dict[str, Any] = next((row for row in history if row["status"] == "applied"), {})
    result["POST api/people/bulk/preview?case=declared-delete"]["proposal"] = LegacyDemoProposal(
        fleet_id=FLEET_ID,
        revision=revision.get("number", 1),
        generation=1,
        plan_hash=revision.get("plan_hash", "0" * 64),
        intent_sha256=revision.get("intent_sha256", "0" * 64),
        usernames=names,
    ).to_data()
    return result


def current_demo_response(body: Any) -> Any:
    """Consume only retired demo fields while retaining strict validation of all other fields."""
    if not isinstance(body, dict):
        return body
    result = {key: value for key, value in body.items() if key not in {"declared", "declared_users", "proposal"}}
    if isinstance(result.get("users"), list):
        result["users"] = [current_demo_response(user) for user in result["users"]]
    return result
