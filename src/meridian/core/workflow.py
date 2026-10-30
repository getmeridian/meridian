"""Workflow contracts for UI-renderable core interactions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from meridian.core.models import CoreModel

InputKind = Literal["text", "boolean", "choice", "confirmation"]


class InputOption(CoreModel):
    """One selectable value for a workflow field."""

    value: str
    label: str
    description: str = ""


class InputField(CoreModel):
    """One UI-renderable input field requested by a workflow."""

    id: str
    label: str
    kind: InputKind
    required: bool = False
    default: Any = None
    help_text: str = ""
    secret: bool = False
    options: list[InputOption] = Field(default_factory=list)


class InputSection(CoreModel):
    """Logical grouping for workflow fields."""

    id: str
    title: str
    description: str = ""
    field_ids: list[str] = Field(default_factory=list)


class WorkflowPlan(CoreModel):
    """Current workflow state for a CLI, UI, or process client."""

    id: str
    title: str
    summary: str
    needs_input: bool
    fields: list[InputField] = Field(default_factory=list)
    sections: list[InputSection] = Field(default_factory=list)
    ready_request_schema: str = ""
