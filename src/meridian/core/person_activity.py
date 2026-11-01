"""Person activity vocabulary and the aggregate evidence for never-used accounts."""

from typing import Literal, Protocol

PersonAttentionReason = Literal["expiring", "quota", "unused", "stopped"]


class PersonActivity(Protocol):
    @property
    def first_traffic_at(self) -> str | None: ...

    @property
    def online_at(self) -> str | None: ...

    @property
    def lifetime_traffic_bytes(self) -> int: ...


def is_never_used(user: PersonActivity) -> bool:
    """No known lifetime traffic or aggregate activity, including undated imported use."""
    return user.first_traffic_at is None and user.online_at is None and user.lifetime_traffic_bytes == 0


class PersonAttentionEvidence(PersonActivity, Protocol):
    @property
    def expire_at(self) -> str | None: ...

    @property
    def created_at(self) -> str: ...

    @property
    def used_traffic_bytes(self) -> int: ...

    @property
    def traffic_limit_bytes(self) -> int: ...
