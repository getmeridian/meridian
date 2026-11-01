"""A control service and HTTP requests for token permission scenarios."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian.control.enrollment import token_sha256
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID


class Clock:
    def __init__(self) -> None:
        self.now = float(int(time.time()))

    def __call__(self) -> float:
        return self.now


@dataclass
class Fleet:
    url: str
    service: ControlService
    clock: Clock


@contextmanager
def serve_tokens(tmp_path: Path) -> Iterator[Fleet]:
    clock = Clock()
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    # A short poll keeps shutdown quick across the scope matrix's many servers.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield Fleet(f"http://127.0.0.1:{server.server_address[1]}", service, clock)
    finally:
        server.shutdown()
        server.server_close()


def token_request(
    url: str, method: str = "GET", body: Any = None, token: str = "", headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")
