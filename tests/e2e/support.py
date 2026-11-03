"""Controllable time and browser fault expectations shared by opt-in journeys."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from playwright.sync_api import ConsoleMessage, Page

OWNER_PASSWORD = "e2e-only-password-not-an-account"


@dataclass
class ServiceClock:
    moment: datetime = datetime(2027, 1, 15, 12, tzinfo=UTC)
    pages: list[Page] = field(default_factory=list)

    def __call__(self) -> float:
        return self.moment.timestamp()

    def advance(self, *, seconds: int) -> None:
        self.moment += timedelta(seconds=seconds)
        for page in self.pages:
            if not page.is_closed():
                page.clock.set_fixed_time(self.moment)


@dataclass
class BrowserFaults:
    unexpected: list[str] = field(default_factory=list)
    expected: list[re.Pattern[str]] = field(default_factory=list)

    def expect(self, pattern: str) -> None:
        """Expect one precise error, including its URL and status when it is an HTTP refusal."""
        self.expected.append(re.compile(pattern))

    def record(self, message: str) -> None:
        for pattern in self.expected:
            if pattern.search(message):
                self.expected.remove(pattern)
                return
        self.unexpected.append(message)

    def console(self, message: ConsoleMessage) -> None:
        if message.type == "error":
            self.record(f"console: {message.location.get('url', '')} {message.text}")
