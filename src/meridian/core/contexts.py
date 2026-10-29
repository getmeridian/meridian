"""Results for `meridian context`: the fleets kept on this machine."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.models import CoreModel


class ContextSummary(CoreModel):
    """One context as local state describes it; nothing here comes from a server."""

    name: str
    current: bool = Field(description="Whether commands without --context work on this fleet")
    path: str = Field(description="The directory holding this fleet's local files, ~ for the home directory")
    control: str = Field(default="", description="The control server's saved address; empty with no fleet yet")
    exits: int = 0
    relays: int = 0
    gateways: int = 0
    state: Literal["ok", "empty", "unreadable"] = Field(
        description="ok: a deployed fleet; empty: nothing deployed yet; unreadable: local files need repair"
    )
    superseded_by: str = Field(
        default="",
        description="The context a restore moved this fleet to ('?' when unknown); this copy no longer changes it",
    )


class ContextListResult(CoreModel):
    """Every context in name order, default first."""

    contexts: list[ContextSummary] = Field(default_factory=list)
    pinned: bool = Field(default=False, description="MERIDIAN_HOME pins one fleet; contexts are unavailable")
