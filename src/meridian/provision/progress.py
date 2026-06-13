"""Step rendering abstractions for provisioning pipelines.

StepRenderer protocol decouples step execution from output presentation.
RichStepRenderer lives here so Rich imports stay out of steps.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from meridian.provision.steps import StepResult


class StepRenderer(Protocol):
    """Callback protocol for rendering provisioning step progress."""

    def step_starting(self, name: str, index: int, total: int) -> Iterator[None]:
        """Context manager shown while a step is running."""
        ...  # pragma: no cover

    def step_completed(self, result: StepResult) -> None:
        """Called when a step finishes with ok or changed status."""
        ...  # pragma: no cover

    def step_failed(self, result: StepResult) -> None:
        """Called when a step finishes with failed status."""
        ...  # pragma: no cover

    def step_skipped(self, result: StepResult) -> None:
        """Called when a step finishes with skipped status."""
        ...  # pragma: no cover


class NoopStepRenderer:
    """Silent renderer for tests and headless use."""

    @contextmanager
    def step_starting(self, name: str, index: int, total: int) -> Iterator[None]:
        yield

    def step_completed(self, result: StepResult) -> None:
        return None

    def step_failed(self, result: StepResult) -> None:
        return None

    def step_skipped(self, result: StepResult) -> None:
        return None


class RichStepRenderer:
    """Rich-based renderer with spinner and colored status markers."""

    @contextmanager
    def step_starting(self, name: str, index: int, total: int) -> Iterator[None]:
        from rich.console import Console
        from rich.status import Status

        console = Console(stderr=True, highlight=False)
        prefix = f"[{index}/{total}]"
        with Status(f"  [cyan]{prefix} {name}[/cyan]", console=console, spinner="dots"):
            yield

    def step_completed(self, result: StepResult) -> None:
        from rich.console import Console

        console = Console(stderr=True, highlight=False)
        marker = "✓"
        detail = f" [dim]({result.detail})[/dim]" if result.detail else ""
        console.print(f"  [green]{marker}[/green] {result.name}{detail}")

    def step_failed(self, result: StepResult) -> None:
        from rich.console import Console

        console = Console(stderr=True, highlight=False)
        detail = f" ({result.detail})" if result.detail else ""
        console.print(f"  [red bold]✗[/red bold] {result.name}{detail}")

    def step_skipped(self, result: StepResult) -> None:
        from rich.console import Console

        console = Console(stderr=True, highlight=False)
        detail = f" ({result.detail})" if result.detail else ""
        console.print(f"  [dim]– {result.name}{detail}[/dim]")
