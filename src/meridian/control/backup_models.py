"""Facts a managing computer can confirm only after opening and saving an encrypted snapshot."""

from pydantic import Field

from meridian.core.agent_types import MAX_SAFE_INTEGER, Timestamp
from meridian.core.models import CoreModel


class SnapshotSaveProof(CoreModel):
    changes: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    proof: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class BackupReceipt(SnapshotSaveProof):
    device_id: str = Field(pattern=r"^[0-9a-f]{12}$")


class BackupStatus(CoreModel):
    changes: int = Field(ge=0)
    last_saved_changes: int | None = Field(default=None, ge=0)
    last_saved_at: Timestamp | None = None
    has_recovery_key: bool
    has_deployer_state: bool
