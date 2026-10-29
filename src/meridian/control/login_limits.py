"""In-memory limits on password, code, and setup-link guesses, per account and per source.

Nothing here is written to disk or logged: a restart forgets every count. A source
is kept only as an HMAC under a key that lives as long as the process, so even
memory holds no client address. Behind nginx's 443 stream router the service sees
every browser as loopback (by design: Meridian never learns where people connect
from), so on today's fleets every source shares one bucket; the account limit is
the one that binds first.

A key that failed `free_failures` times in a row is locked for a delay that doubles
with each further failure, up to `max_lock_seconds`. A locked key is refused before
any password is hashed or code compared, so a lock is also cheap to enforce. Success
clears the account's and the source's failures.

Guesses at one account take turns (`turn`): the check, the slow hash, and the count
happen while the turn is held, so concurrent requests cannot all pass the check
before the first failure is counted. Hashing is one at a time anyway, so turns cost
no throughput.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from meridian.control.errors import ServiceError

ACCOUNT_FREE_FAILURES = 5
SOURCE_FREE_FAILURES = 10
BASE_LOCK_SECONDS = 30
MAX_LOCK_SECONDS = 15 * 60
# Bounds memory under a flood of distinct sources; the oldest entries go first.
MAX_TRACKED_KEYS = 10_000


@dataclass
class _Failures:
    count: int = 0
    locked_until: float = 0.0


class LoginLimiter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        account_free_failures: int = ACCOUNT_FREE_FAILURES,
        source_free_failures: int = SOURCE_FREE_FAILURES,
        max_lock_seconds: int = MAX_LOCK_SECONDS,
    ) -> None:
        self._clock = clock
        self._free = {"account": account_free_failures, "source": source_free_failures}
        self._max_lock = max_lock_seconds
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._failures: OrderedDict[str, _Failures] = OrderedDict()
        # One turn lock per account with a guess in progress or waiting, and how many hold or await it.
        self._turns: dict[str, tuple[threading.Lock, int]] = {}

    @contextmanager
    def turn(self, account: str, source: str) -> Iterator[None]:
        """Wait for the account's turn, then refuse while locked; count the guess before the block ends."""
        with self._lock:
            lock, users = self._turns.get(account, (threading.Lock(), 0))
            self._turns[account] = (lock, users + 1)
        try:
            with lock:
                self.check(account, source)
                yield
        finally:
            with self._lock:
                lock, users = self._turns[account]
                if users == 1:
                    del self._turns[account]
                else:
                    self._turns[account] = (lock, users - 1)

    def check(self, account: str, source: str) -> None:
        """Refuse with 429 and the wait while either key is locked."""
        now = self._clock()
        with self._lock:
            waits = [
                entry.locked_until - now
                for entry in (self._failures.get(key) for key in self._keys(account, source))
                if entry is not None and entry.locked_until > now
            ]
        if waits:
            raise LockedOut(math.ceil(max(waits)))

    def failed(self, account: str, source: str) -> None:
        now = self._clock()
        with self._lock:
            for kind, key in zip(("account", "source"), self._keys(account, source), strict=True):
                entry = self._failures.pop(key, None) or _Failures()
                entry.count += 1
                over = entry.count - self._free[kind]
                if over >= 0:
                    entry.locked_until = now + min(self._max_lock, BASE_LOCK_SECONDS * 2**over)
                self._failures[key] = entry
            while len(self._failures) > MAX_TRACKED_KEYS:
                self._failures.popitem(last=False)

    def succeeded(self, account: str, source: str) -> None:
        with self._lock:
            for key in self._keys(account, source):
                self._failures.pop(key, None)

    def _keys(self, account: str, source: str) -> tuple[str, str]:
        digest = hmac.new(self._key, source.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"account:{account}", f"source:{digest}"


class LockedOut(ServiceError):
    """Too many failures: try again after `retry_after` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("rate_limited", "Too many attempts. Try again later.", 429)
        self.retry_after = max(1, retry_after)
