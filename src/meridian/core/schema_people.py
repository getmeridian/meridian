"""The service's private person contracts shared with administrative clients."""

from __future__ import annotations

from pydantic import BaseModel

from meridian.control.models import UserImport, UserImportBatch, UserPatch, UserRecord, UserUpdate

PEOPLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "user-record": UserRecord,
    "user-update": UserUpdate,
    "user-patch": UserPatch,
    "user-import": UserImport,
    "user-import-batch": UserImportBatch,
}
