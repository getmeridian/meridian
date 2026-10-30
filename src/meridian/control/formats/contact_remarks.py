"""Optional owner instructions in bounded status placeholder remarks."""

from __future__ import annotations

from meridian.control.models import ENDPOINT_REMARK_MAX_LENGTH


def contact_remarks(remarks: tuple[str, ...], text: str) -> tuple[str, ...]:
    """Keep every original notice when the full text cannot fit in every remark."""
    if not text:
        return remarks
    updated = tuple(f"{remark} · {text}" for remark in remarks)
    return updated if all(len(remark) <= ENDPOINT_REMARK_MAX_LENGTH for remark in updated) else remarks
