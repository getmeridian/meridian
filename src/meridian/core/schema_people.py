"""The service's private person contracts shared with administrative clients."""

from __future__ import annotations

from pydantic import BaseModel

from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.models import UserImport, UserImportBatch, UserPatch, UserRecord, UserUpdate
from meridian.core.account_schedule import AccountLifecycleSettings, AccountSchedulePreview

PEOPLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "account-lifecycle-settings": AccountLifecycleSettings,
    "account-schedule-preview": AccountSchedulePreview,
    "user-schedule-preview-request": UserSchedulePreviewRequest,
    "user-record": UserRecord,
    "user-update": UserUpdate,
    "user-patch": UserPatch,
    "user-import": UserImport,
    "user-import-batch": UserImportBatch,
}
