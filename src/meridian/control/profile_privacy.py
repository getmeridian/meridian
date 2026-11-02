"""Initial membership in reviewed creation settings is private like live service membership."""

from __future__ import annotations

import copy

from meridian.control.models import RevisionRecord


def visible_profile_revision(record: RevisionRecord, *, private_allowed: bool) -> RevisionRecord:
    if private_allowed:
        return record
    intent = copy.deepcopy(record.intent)
    access = intent.get("access", {})
    settings = access.get("creation_settings", {}) if isinstance(access, dict) else {}
    if isinstance(settings, dict):
        for person in settings.values():
            if isinstance(person, dict):
                person.pop("profiles", None)
    return RevisionRecord.model_validate({**record.to_data(), "intent": intent})
