"""Reviewed app rules persist atomically beside the other service delivery settings."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from meridian.control.store import Store
from meridian.core.response_rules import ResponseRule, ResponseRuleSettings


def read_response_rules(store: Store) -> list[ResponseRule]:
    return TypeAdapter(list[ResponseRule]).validate_json(store.meta("response_rules") or "[]")


def save_response_rules(store: Store, settings: ResponseRuleSettings) -> None:
    store.set_meta(
        "response_rules",
        json.dumps([rule.to_data() for rule in settings.response_rules], ensure_ascii=False, sort_keys=True),
    )
