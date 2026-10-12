"""Cooperative cancellation contracts shared by Engine, CLI, and reconciler."""

from __future__ import annotations

import pytest

from meridian.core.cancellation import (
    CancellationFlag,
    OperationCancellation,
    OperationCancelledError,
    raise_if_cancelled,
)
from meridian.core.events import COMMAND_CANCEL_REQUESTED, COMMAND_CANCELLED, EVENT_TYPES
from meridian.core.output import command_envelope


def test_raise_if_cancelled_is_a_no_op_until_the_flag_is_set() -> None:
    flag = CancellationFlag()
    raise_if_cancelled(None, phase="apply", message="unused")
    raise_if_cancelled(flag, phase="apply", message="unused")

    flag.cancel()
    with pytest.raises(OperationCancelledError) as exc_info:
        raise_if_cancelled(flag, phase="verify", message="Stopped before verification.", remote_changed=True)

    cancellation = exc_info.value.cancellation
    assert cancellation.phase == "verify"
    assert cancellation.remote_changed is True
    assert str(exc_info.value) == "Stopped before verification."


def test_cancelled_error_is_retryable_and_never_uses_the_interrupt_exit_code() -> None:
    error = OperationCancelledError(OperationCancellation(phase="queued", message="Cancelled before start."))

    assert error.category == "cancelled"
    assert error.retryable is True
    assert error.exit_code == 5
    assert error.to_error_model().category == "cancelled"


def test_cancel_event_names_are_part_of_the_public_set() -> None:
    assert COMMAND_CANCEL_REQUESTED in EVENT_TYPES
    assert COMMAND_CANCELLED in EVENT_TYPES


def test_deploy_envelope_accepts_a_typed_cancelled_terminal_variant() -> None:
    cancellation = OperationCancellation(phase="apply", message="Stopped after 3 of 9 actions.", remote_changed=True)
    error = OperationCancelledError(cancellation)

    envelope = command_envelope(
        command="deploy",
        data=cancellation.model_dump(mode="json"),
        summary=str(error),
        status="cancelled",
        exit_code=error.exit_code,
        errors=[error.to_error_model()],
    )

    assert envelope.status == "cancelled"
    assert envelope.data["remote_changed"] is True


def test_deploy_cancelled_envelope_requires_cancellation_data() -> None:
    error = OperationCancelledError(OperationCancellation(phase="apply", message="Stopped."))

    with pytest.raises(ValueError):
        command_envelope(
            command="deploy",
            data={},
            summary="Stopped.",
            status="cancelled",
            exit_code=5,
            errors=[error.to_error_model()],
        )
