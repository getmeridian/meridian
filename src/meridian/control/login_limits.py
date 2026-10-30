"""In-memory limits on password, code, and setup-link guesses, one bucket per kind of guess.

Nothing here is written to disk or logged: a restart forgets every count. There is
no per-source bucket: behind nginx's 443 stream router the service sees every
browser as loopback (by design: Meridian never learns where people connect from),
so a "source" would be one bucket every guess shares, and failures at one door
would lock the others. The buckets are instead what is being guessed:

- `owner` — the password (and code) at sign-in, before any session exists;
- `setup-link` — setup-link tokens, so guessing links never locks sign-in;
- `session:<id>` — a signed-in browser re-entering its password or confirming a new
  authenticator, so failed sign-ins never lock out an owner already signed in.

A bucket that failed `free_failures` times in a row is locked for a delay that
doubles with each further failure, up to `max_lock_seconds`. A locked bucket is
refused before any password is hashed or code compared, so a lock is also cheap to
enforce. Success clears the bucket.

Guesses in one bucket take turns (`turn`): the check, the slow hash, and the count
happen while the turn is held, so concurrent requests cannot all pass the check
before the first failure is counted. Hashing is one at a time anyway, so turns cost
no throughput.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from meridian.control.errors import ServiceError

FREE_FAILURES = 5
BASE_LOCK_SECONDS = 30
MAX_LOCK_SECONDS = 15 * 60
# Bounds memory (one bucket per session at most); the oldest entries go first.
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
        free_failures: int = FREE_FAILURES,
        max_lock_seconds: int = MAX_LOCK_SECONDS,
    ) -> None:
        self._clock = clock
        self._free = free_failures
        self._max_lock = max_lock_seconds
        self._lock = threading.Lock()
        self._failures: OrderedDict[str, _Failures] = OrderedDict()
        # One turn lock per bucket with a guess in progress or waiting, and how many hold or await it.
        self._turns: dict[str, tuple[threading.Lock, int]] = {}

    @contextmanager
    def turn(self, bucket: str) -> Iterator[None]:
        """Wait for the bucket's turn, then refuse while locked; count the guess before the block ends."""
        with self._lock:
            lock, users = self._turns.get(bucket, (threading.Lock(), 0))
            self._turns[bucket] = (lock, users + 1)
        try:
            with lock:
                self.check(bucket)
                yield
        finally:
            with self._lock:
                lock, users = self._turns[bucket]
                if users == 1:
                    del self._turns[bucket]
                else:
                    self._turns[bucket] = (lock, users - 1)

    def check(self, bucket: str) -> None:
        """Refuse with 429 and the wait while the bucket is locked."""
        now = self._clock()
        with self._lock:
            entry = self._failures.get(bucket)
            wait = entry.locked_until - now if entry is not None else 0.0
        if wait > 0:
            raise LockedOut(math.ceil(wait))

    def failed(self, bucket: str) -> None:
        now = self._clock()
        with self._lock:
            entry = self._failures.pop(bucket, None) or _Failures()
            entry.count += 1
            over = entry.count - self._free
            if over >= 0:
                entry.locked_until = now + min(self._max_lock, BASE_LOCK_SECONDS * 2**over)
            self._failures[bucket] = entry
            while len(self._failures) > MAX_TRACKED_KEYS:
                self._failures.popitem(last=False)

    def succeeded(self, bucket: str) -> None:
        with self._lock:
            self._failures.pop(bucket, None)


class LockedOut(ServiceError):
    """Too many failures: try again after `retry_after` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("rate_limited", "Too many attempts. Try again later.", 429)
        self.retry_after = max(1, retry_after)
