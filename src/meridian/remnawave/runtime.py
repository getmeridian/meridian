"""Synchronous SDK bridge and typed Remnawave errors."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
from remnawave.exceptions import ApiError, ForbiddenError, NetworkError, NotFoundError, UnauthorizedError

from meridian.core.errors import MeridianError


class RemnawaveError(MeridianError):
    """Raised when a Remnawave API call fails."""


class RemnawaveNotFoundError(RemnawaveError):
    """Resource not found (404)."""


class RemnawaveAuthError(RemnawaveError):
    """Authentication or authorization failure (401/403)."""


class RemnawaveNetworkError(RemnawaveError):
    """Transport-level failure (connection refused, timeout)."""


_thread_loops = threading.local()


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously.

    Uses a per-thread persistent event loop to avoid closing the SDK's
    httpx.AsyncClient between calls (which causes "Event loop is closed"
    errors on subsequent operations). ``threading.local()`` isolates the
    loop per thread so parallel workers in ``execute_plan()`` each get
    their own loop and never cross-pollinate.
    """
    loop = getattr(_thread_loops, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_loops.loop = loop
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# SDK error → Meridian error mapping
# ---------------------------------------------------------------------------


def sdk_call(coro: Any) -> Any:
    """Run an async SDK coroutine, converting SDK exceptions to Meridian types.

    All SDK-backed methods should use this instead of bare ``_run()``.
    """
    try:
        return _run(coro)
    except (RemnawaveError, RemnawaveNotFoundError, RemnawaveAuthError, RemnawaveNetworkError):
        raise
    except NetworkError as e:
        raise RemnawaveNetworkError(f"Panel network error: {e}", category="system") from e
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if status_code in (401, 403):
            raise RemnawaveAuthError(
                f"Panel authentication failed: {e}",
                hint="Check your API token — it may have expired",
                category="user",
            ) from e
        if status_code == 404:
            raise RemnawaveNotFoundError(f"Resource not found: {e}", category="system") from e
        raise RemnawaveError(f"Panel API error: {e}", category="system") from e
    except httpx.RequestError as e:
        raise RemnawaveNetworkError(f"Panel network error: {e}", category="system") from e
    except ApiError as e:
        if isinstance(e, NotFoundError):
            raise RemnawaveNotFoundError(f"Resource not found: {e}", category="system") from e
        if isinstance(e, (UnauthorizedError, ForbiddenError)):
            raise RemnawaveAuthError(
                f"Panel authentication failed: {e}",
                hint="Check your API token — it may have expired",
                category="user",
            ) from e
        if isinstance(e, ApiError):
            raise RemnawaveError(f"Panel API error: {e}", category="system") from e
        raise
