"""A fleet-specific host tag, carried only inside the encrypted recovery file."""

from __future__ import annotations

import hmac
from pathlib import Path
from uuid import UUID

from meridian.servers import this_machine_id


def fleet_host_fingerprint(fleet_id: str) -> str | None:
    # Linux's boot ID survives NAT and is also shared by ordinary containers on this
    # kernel. The systemd ID covers installations that do not mount procfs. Mark which
    # kind of evidence we have: different sources cannot prove two hosts are distinct.
    try:
        identity = UUID(Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()).hex
        kind = "1"
    except (OSError, ValueError):
        identity, kind = this_machine_id(), "2"
        if not identity:
            return None
    digest = hmac.digest(bytes.fromhex(identity), b"meridian:backup:source:v1:" + fleet_id.encode(), "sha256").hex()
    return kind + digest[1:]
