"""Shared event names for meridian-core progress streams."""

from __future__ import annotations

from typing import Final, Literal

CoreEventType = Literal[
    "command.started",
    "command.completed",
    "command.failed",
    "plan.computed",
    "plan.action.started",
    "plan.action.completed",
    "plan.action.failed",
    "plan.action.skipped",
    "provision.step.started",
    "provision.step.completed",
    "provision.step.failed",
    "ssh.command.completed",
    "state.loaded",
    "state.saved",
    "warning",
    "error",
]

COMMAND_STARTED: Final[CoreEventType] = "command.started"
COMMAND_COMPLETED: Final[CoreEventType] = "command.completed"
COMMAND_FAILED: Final[CoreEventType] = "command.failed"

PLAN_COMPUTED: Final[CoreEventType] = "plan.computed"
PLAN_ACTION_STARTED: Final[CoreEventType] = "plan.action.started"
PLAN_ACTION_COMPLETED: Final[CoreEventType] = "plan.action.completed"
PLAN_ACTION_FAILED: Final[CoreEventType] = "plan.action.failed"
PLAN_ACTION_SKIPPED: Final[CoreEventType] = "plan.action.skipped"

PROVISION_STEP_STARTED: Final[CoreEventType] = "provision.step.started"
PROVISION_STEP_COMPLETED: Final[CoreEventType] = "provision.step.completed"
PROVISION_STEP_FAILED: Final[CoreEventType] = "provision.step.failed"

SSH_COMMAND_COMPLETED: Final[CoreEventType] = "ssh.command.completed"
STATE_LOADED: Final[CoreEventType] = "state.loaded"
STATE_SAVED: Final[CoreEventType] = "state.saved"
WARNING: Final[CoreEventType] = "warning"
ERROR: Final[CoreEventType] = "error"

EVENT_TYPES: Final[tuple[CoreEventType, ...]] = (
    COMMAND_STARTED,
    COMMAND_COMPLETED,
    COMMAND_FAILED,
    PLAN_COMPUTED,
    PLAN_ACTION_STARTED,
    PLAN_ACTION_COMPLETED,
    PLAN_ACTION_FAILED,
    PLAN_ACTION_SKIPPED,
    PROVISION_STEP_STARTED,
    PROVISION_STEP_COMPLETED,
    PROVISION_STEP_FAILED,
    SSH_COMMAND_COMPLETED,
    STATE_LOADED,
    STATE_SAVED,
    WARNING,
    ERROR,
)
