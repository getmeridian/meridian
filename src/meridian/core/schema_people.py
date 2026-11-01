"""The service's private person contracts shared with administrative clients."""

from __future__ import annotations

from pydantic import BaseModel

from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.credential_models import UserCredentialRotation
from meridian.control.models import UserImport, UserImportBatch, UserPatch, UserRecord, UserUpdate
from meridian.core.account_schedule import AccountLifecycleSettings, AccountSchedulePreview
from meridian.core.client_sessions import CredentialRotationRequest, UserFlowBlocks
from meridian.core.people_bulk import (
    BulkAction,
    BulkCommitRequest,
    BulkPreview,
    BulkPreviewRequest,
    BulkReceipt,
    BulkUndoRequest,
    ClientBulkResult,
)
from meridian.core.people_query import PeopleFacets, PeoplePage, PeoplePerson, PeopleQuery

PEOPLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "people-query": PeopleQuery,
    "people-page": PeoplePage,
    "people-person": PeoplePerson,
    "people-facets": PeopleFacets,
    "bulk-action": BulkAction,
    "bulk-preview-request": BulkPreviewRequest,
    "bulk-preview": BulkPreview,
    "bulk-commit-request": BulkCommitRequest,
    "bulk-receipt": BulkReceipt,
    "bulk-undo-request": BulkUndoRequest,
    "client-bulk": ClientBulkResult,
    "account-lifecycle-settings": AccountLifecycleSettings,
    "account-schedule-preview": AccountSchedulePreview,
    "user-schedule-preview-request": UserSchedulePreviewRequest,
    "user-record": UserRecord,
    "user-update": UserUpdate,
    "user-patch": UserPatch,
    "user-import": UserImport,
    "user-import-batch": UserImportBatch,
    "user-flow-blocks": UserFlowBlocks,
    "credential-rotation-request": CredentialRotationRequest,
    "user-credential-rotation": UserCredentialRotation,
}
