"""Owner backup visibility and a confirmed off-server save from the managing CLI."""

import re

from meridian.control.api_models import SnapshotResponse
from meridian.control.backup_models import BackupReceipt, BackupStatus
from meridian.control.errors import ServiceError
from meridian.control.route_metadata import CONFLICT, NOT_FOUND
from meridian.control.route_types import ErrorResponse, route
from meridian.control.times import parse_timestamp
from meridian.control.tokens import unauthenticated
from meridian.control.ui_routes import UiRequest


def _still_allowed(request: UiRequest) -> None:
    service = request.ui.service
    if request.owner_session:
        service.owner.hold_recent(request.owner_session)
    elif request.caller and request.caller.token_id:
        token = service.tokens.get(request.caller.token_id)
        if token.revoked_at or (
            token.expires_at and parse_timestamp(token.expires_at) <= parse_timestamp(service.now())
        ):
            raise unauthenticated()


def get_backup_status(request: UiRequest, _match: re.Match[str]) -> BackupStatus:
    return request.ui.service.backups.status()


def record_backup_save(request: UiRequest, _match: re.Match[str]) -> BackupStatus:
    receipt = BackupReceipt.model_validate_json(request.read_body())
    service = request.ui.service
    with service.conditions(lambda: _still_allowed(request)):
        return service.backups.record_save(receipt)


def download_backup(request: UiRequest, _match: re.Match[str]) -> None:
    if request.read_body():
        raise ServiceError("invalid_request", "A backup download accepts no keys or other inputs.", 400)
    service = request.ui.service
    data = service.snapshot(for_download=True, authorize=lambda: _still_allowed(request))
    # This event reports an export, never a confirmed save. Recheck after encryption,
    # before returning bytes, in case the owner session ended during preparation.
    with service.conditions(lambda: _still_allowed(request)):
        assert request.caller is not None
        service.backups.record_export(service.tokens.actor(request.caller).name)
    request.respond(
        200,
        "application/octet-stream",
        data,
        {
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="backup.snapshot"',
            "X-Content-Type-Options": "nosniff",
        },
    )


BACKUP_ROUTES = (
    route(
        "GET",
        "/api/backup/status",
        "admin",
        get_backup_status,
        None,
        BackupStatus,
        "Read backup freshness and the last confirmed save to a managing computer.",
        credentials=False,
        capability="backup.read",
    ),
    route(
        "POST",
        "/api/backup/receipts",
        "admin",
        record_backup_save,
        BackupReceipt,
        BackupStatus,
        "Confirm a verified snapshot was saved by a registered managing computer.",
        credentials=False,
        capability="backup.edit",
        refusals=(CONFLICT, NOT_FOUND),
    ),
    route(
        "POST",
        "/api/backup/download",
        "admin",
        download_backup,
        None,
        SnapshotResponse,
        "Export a recoverable snapshot to registered keys after owner confirmation.",
        credentials=True,
        capability="backup.read",
        content_type="application/octet-stream",
        refusals=(CONFLICT, ErrorResponse(400, ("invalid_request",))),
    ),
)
