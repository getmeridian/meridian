"""Interactive rendering of reviewed-apply progress events."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from meridian.commands import apply_progress
from meridian.commands.apply_progress import RichApplyReporter
from meridian.core.models import Event, ResourceRef
from meridian.core.output import OperationContext


@pytest.fixture
def stderr(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buffer = io.StringIO()
    monkeypatch.setattr(apply_progress, "err_console", Console(file=buffer, width=120, highlight=False))
    return buffer


def _event(event_type: str, kind: str, server: str, *, index: int, total: int, **data: object) -> Event:
    return OperationContext().events.event(
        event_type,  # type: ignore[arg-type]
        resource=ResourceRef(kind=kind, id=f"{kind}:x", name=server),
        message=str(data.pop("message", "")),
        data={"index": index, "total": total, **data},
    )


def test_converged_actions_stay_silent_and_changes_name_the_server(stderr: io.StringIO) -> None:
    reporter = RichApplyReporter(server_label={"srv-1": "server-1"}.get)

    reporter.emit(_event("plan.action.started", "firewall_rule", "srv-1", index=1, total=2))
    reporter.emit(_event("plan.action.completed", "firewall_rule", "srv-1", index=1, total=2, status="converged"))
    reporter.emit(_event("plan.action.started", "server_baseline", "srv-1", index=2, total=2))
    reporter.emit(
        _event("plan.action.completed", "server_baseline", "srv-1", index=2, total=2, status="applied", changed=True)
    )

    lines = stderr.getvalue().splitlines()
    assert lines == ["  ✓ Prepare server on server-1"]


def test_failure_line_carries_the_driver_error_and_stops_the_spinner(stderr: io.StringIO) -> None:
    reporter = RichApplyReporter()

    reporter.emit(_event("plan.action.started", "certificate", "", index=1, total=1))
    reporter.emit(
        _event("plan.action.failed", "certificate", "", index=1, total=1, status="failed", message="acme.sh timed out")
    )

    assert "✗ TLS certificate: acme.sh timed out" in stderr.getvalue()
    assert reporter._status is None


def test_progress_speaks_the_wizard_language_while_events_stay_english(stderr: io.StringIO) -> None:
    from meridian import i18n

    i18n.set_locale("ru")
    try:
        reporter = RichApplyReporter(server_label={"srv-1": "otter"}.get)
        reporter.emit(_event("plan.action.started", "server_baseline", "srv-1", index=1, total=1))
        reporter.emit(OperationContext().events.event("provision.step.started", message="Install Docker"))
        assert reporter._action == "[1/1] Подготовка сервера: otter"
        done = {"status": "applied", "changed": True}
        reporter.emit(_event("plan.action.completed", "server_baseline", "srv-1", index=1, total=1, **done))
    finally:
        i18n.set_locale("en")

    assert stderr.getvalue().splitlines() == ["  ✓ Подготовка сервера: otter"]
