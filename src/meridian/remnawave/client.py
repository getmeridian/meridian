"""Read-only Remnawave API client: `meridian migrate` reads a panel's users before it goes."""

from __future__ import annotations

import logging
import random
import ssl
import time
from typing import Any

import httpx

from .models import User, parse_user, response_items
from .runtime import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)

logger = logging.getLogger("meridian.api")

_EMPTY_BODY_STATUSES = frozenset({202, 204})
_USERS_PAGE_SIZE = 1000  # Remnawave rejects larger pages
# What Meridian's Remnawave fleets wrote on every access user; migrate reports the rest as not its own.
ACCESS_USER_DESCRIPTION = "Managed by Meridian access"
ACCESS_USER_TAG = "MERIDIAN_ACCESS"


class RemnawaveReader:
    """Every request goes through ``_request`` (retry, typed errors, envelope unwrapping)."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: float = 30,
        max_retries: int = 5,
        verify: bool | str | ssl.SSLContext = True,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        authorization = api_token if api_token.startswith("Bearer ") else f"Bearer {api_token}"
        self._client = httpx.Client(
            base_url=self._base + "/",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=verify,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> RemnawaveReader:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # --- Low-level raw request ---

    def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        """Make an API request with retry logic.

        Returns the unwrapped ``response`` envelope, or ``None`` for empty
        2xx bodies (Remnawave 3.x answers DELETE/bulk calls with 204 and
        node restarts with 202).
        """
        path = path.lstrip("/")
        logger.debug("%s %s", method, path)
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.request(method, path, json=json, params=params)
                if resp.status_code == 401:
                    raise RemnawaveAuthError(
                        "Panel authentication failed (401)",
                        hint="Check your API token — it may have expired",
                        category="user",
                    )
                if resp.status_code == 403:
                    raise RemnawaveAuthError(
                        "Panel access forbidden (403)",
                        hint="API token lacks required permissions",
                        category="user",
                    )
                if resp.status_code == 404:
                    raise RemnawaveNotFoundError(f"Resource not found: {path}", category="system")
                if resp.status_code >= 500:
                    last_error = RemnawaveError(f"Panel server error ({resp.status_code}): {resp.text[:200]}")
                    if attempt < self._max_retries - 1:
                        time.sleep(min(2**attempt, 16) + random.uniform(0, 1))
                        continue
                    raise last_error
                resp.raise_for_status()
                if resp.status_code in _EMPTY_BODY_STATUSES or not resp.content.strip():
                    return None
                try:
                    data = resp.json()
                except (ValueError, UnicodeDecodeError) as e:
                    raise RemnawaveError(
                        f"Panel returned invalid JSON ({len(resp.content)} bytes)",
                        hint=f"Response may be truncated by firewall or DPI: {e}",
                        category="system",
                    ) from e
                if isinstance(data, dict) and "response" in data:
                    return data["response"]
                return data
            except httpx.ConnectError as e:
                last_error = RemnawaveNetworkError(
                    f"Cannot connect to panel at {self._base}",
                    hint=f"Is the panel running? Check: curl {self._base}/api/health\n{e}",
                    category="system",
                )
                if attempt < self._max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            except httpx.TimeoutException as e:
                last_error = RemnawaveNetworkError(
                    f"Panel request timed out after {self._timeout}s",
                    hint=f"Panel may be overloaded or unreachable: {e}",
                    category="system",
                )
                if attempt < self._max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            except httpx.HTTPStatusError as e:
                raise RemnawaveError(f"Panel API error: {e.response.status_code} {e.response.text[:200]}") from e

        if last_error:
            raise last_error
        raise RemnawaveError("Request failed after retries")

    def list_users(self) -> list[User]:
        """List all users."""
        all_users: list[User] = []
        start = 0
        while True:
            data = self._request("GET", "/api/users", params={"start": start, "size": _USERS_PAGE_SIZE})
            users_list = response_items(data, "users")
            if not users_list:
                break
            all_users.extend(parse_user(u) for u in users_list)
            if len(users_list) < _USERS_PAGE_SIZE:
                break
            start += _USERS_PAGE_SIZE
        return all_users
