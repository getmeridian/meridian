"""A count-only, read-only projection of opt-in device registrations."""

from meridian.control.models import PrivacySettings, UserRecord
from meridian.control.store import Store
from meridian.core.device_slots import DeviceSlots


def read_device_slots(store: Store, user: UserRecord, privacy: PrivacySettings) -> DeviceSlots | None:
    """Called inside the subscription view lock; off never even reads device rows."""
    if privacy.device_limits not in {"private", "full"}:
        return None
    allowed = user.device_limit if user.device_limit is not None else privacy.default_device_limit
    if allowed < 1:
        return None
    row = store.db.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (user.user_id,)).fetchone()
    return DeviceSlots(used=int(row[0]), allowed=allowed)
