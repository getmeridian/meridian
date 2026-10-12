"""Typed per-hop relay health result for `meridian relay check`."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from meridian.core.models import CoreModel
from meridian.core.verification import (
    VerificationCheck,
    VerificationCompletedExitCode,
    VerificationCounts,
    VerificationVerdict,
)


class RelayHopTarget(CoreModel):
    """One Realm hop of a relay chain, resolved from the compiled topology."""

    logical_id: str
    chain_ref: str
    hop_index: int = Field(ge=0)
    server_ref: str
    host: str
    service: str
    listen_port: int = Field(ge=1, le=65535)
    public: bool
    next_host: str
    next_port: int = Field(ge=1, le=65535)


class RelayCheckResult(CoreModel):
    """Health of every checked hop: unit state, next-hop TCP, and public listener."""

    relay: str
    chains: list[str] = Field(default_factory=list)
    hops: list[RelayHopTarget] = Field(default_factory=list)
    verdict: VerificationVerdict
    exit_code: VerificationCompletedExitCode
    counts: VerificationCounts
    checks: list[VerificationCheck] = Field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)
