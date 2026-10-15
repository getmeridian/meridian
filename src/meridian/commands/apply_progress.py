"""Rich rendering of reviewed-apply progress events for interactive commands."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import TracebackType

from rich.markup import escape
from rich.status import Status

from meridian.console import err_console, is_json_mode, is_quiet_mode
from meridian.core.events import (
    PLAN_ACTION_COMPLETED,
    PLAN_ACTION_FAILED,
    PLAN_ACTION_SKIPPED,
    PLAN_ACTION_STARTED,
    PROVISION_STEP_STARTED,
    WARNING,
)
from meridian.core.models import Event
from meridian.core.reporters import NoopReporter, Reporter
from meridian.i18n import step_label, t
from meridian.reconciler.stall_retry import CONNECTION_STALLED
from meridian.servers import ServerRegistry

_TERMINAL = frozenset({PLAN_ACTION_COMPLETED, PLAN_ACTION_FAILED, PLAN_ACTION_SKIPPED})


class RichApplyReporter:
    """Show the running action as a spinner and keep a line for every change or failure.

    Actions that were already converged stay silent, so a no-op re-apply prints
    nothing but the caller's summary.
    """

    def __init__(self, *, server_label: Callable[[str], str] | None = None) -> None:
        self._server_label = server_label or (lambda ref: ref)
        self._status: Status | None = None
        self._action = ""

    def __enter__(self) -> RichApplyReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def emit(self, event: Event) -> None:
        if event.type == PLAN_ACTION_STARTED:
            self._action = f"[{event.data.get('index')}/{event.data.get('total')}] {self._label(event)}"
            self._show(self._action)
        elif event.type == PROVISION_STEP_STARTED and self._action:
            self._show(f"{self._action} — {step_label(event.message)}")
        elif event.type == PLAN_ACTION_COMPLETED:
            if event.data.get("changed") or event.data.get("status") == "applied":
                self._line(f"  [ok]✓[/ok] {escape(self._label(event))}")
        elif event.type == PLAN_ACTION_FAILED:
            detail = f": {event.message}" if event.message else ""
            self._line(f"  [error]✗[/error] {escape(self._label(event) + detail)}")
        elif event.type == PLAN_ACTION_SKIPPED:
            self._line(f"  [warn]-[/warn] {escape(t('progress.waits', label=self._label(event)))}")
        elif event.type == WARNING and event.data.get("code") == CONNECTION_STALLED:
            label = f"{self._label(event)} — " if event.resource is not None else ""
            self._line(f"  [warn]↻[/warn] {escape(label + t('progress.stalled'))}")
        if event.type in _TERMINAL and event.data.get("index") == event.data.get("total"):
            self._action = ""
            self.close()

    def _label(self, event: Event) -> str:
        if event.resource is None:
            return event.message
        label = t(f"progress.kind.{event.resource.kind}")
        if event.resource.name:
            return t("progress.on", label=label, server=self._server_label(event.resource.name))
        return label

    def _show(self, text: str) -> None:
        if self._status is None:
            self._status = err_console.status(escape(text), spinner="dots")
            self._status.start()
        else:
            self._status.update(escape(text))

    def _line(self, markup: str) -> None:
        err_console.print(markup)


@contextmanager
def apply_progress(
    *,
    machine_output: bool = False,
    registry: ServerRegistry | None = None,
) -> Iterator[Reporter]:
    """Yield the interactive progress renderer (silent for JSON/quiet output) and stop it on exit."""
    if machine_output or is_json_mode() or is_quiet_mode():
        yield NoopReporter()
        return
    with RichApplyReporter(server_label=registry_server_label(registry) if registry else None) as reporter:
        yield reporter


def registry_server_label(registry: ServerRegistry) -> Callable[[str], str]:
    """Resolve saved ``srv-*`` refs to the names operators typed."""

    def label(server_ref: str) -> str:
        entry = registry.find(server_ref)
        return (entry.name or entry.host) if entry is not None else server_ref

    return label
