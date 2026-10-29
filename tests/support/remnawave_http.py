"""Remnawave's user endpoints over a mocked HTTP transport, so tests exercise `MeridianPanel`'s real wire shapes."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meridian.remnawave import MeridianPanel
from meridian.remnawave.models import NEVER_EXPIRES_AT

GB = 1024**3


class FakeRemnawave:
    """The user endpoints Meridian calls; unknown body keys are dropped, as Zod does."""

    _UPDATABLE = ("status", "trafficLimitBytes", "trafficLimitStrategy", "expireAt", "activeInternalSquads")

    def __init__(self, *usernames: str) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        for username in usernames:
            self.add(username)

    def add(self, username: str) -> None:
        user_id = 101 + len(self.users)
        self.users[username] = {
            "id": user_id,
            "username": username,
            "shortUuid": f"short-{username}",
            "vlessUuid": f"550e8400-e29b-41d4-a716-4466554400{user_id}",
            "status": "ACTIVE",
            "trafficLimitBytes": 0,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": NEVER_EXPIRES_AT,
            "createdAt": "2026-04-01T12:00:00.000Z",
            "userTraffic": {"usedTrafficBytes": int(12.4 * GB), "onlineAt": None},
        }

    def panel(self, *_args: object, **_kwargs: object) -> MeridianPanel:
        panel = MeridianPanel("https://198.51.100.1/panel", "test-token", max_retries=1)
        panel._client = httpx.Client(
            base_url="https://198.51.100.1/panel/", transport=httpx.MockTransport(self._handle)
        )
        return panel

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/panel")
        self.requests.append((request.method, path, body))
        if request.method == "GET" and path.startswith("/api/users/by-username/"):
            user = self.users.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"response": user}) if user else httpx.Response(404)
        if request.method == "PATCH" and path == "/api/users":
            user = self._by_id(body["id"])
            user.update({key: value for key, value in body.items() if key in self._UPDATABLE})
            return httpx.Response(200, json={"response": user})
        if request.method == "POST" and path.endswith("/actions/reset-traffic"):
            user = self._by_id(int(path.split("/")[3]))
            user["userTraffic"]["usedTrafficBytes"] = 0
            if user["status"] == "LIMITED":
                user["status"] = "ACTIVE"
            return httpx.Response(200, json={"response": user})
        return httpx.Response(404)

    def _by_id(self, user_id: int) -> dict[str, Any]:
        return next(user for user in self.users.values() if user["id"] == user_id)
