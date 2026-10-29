"""A real control service (`meridian serve`) on a loopback port, for adapter and command tests."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from meridian.control.enrollment import token_sha256
from meridian.control.server import ControlServer
from meridian.control.service import ControlService

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"


@contextmanager
def serve_control(state_dir: Path) -> Iterator[str]:
    """Run a control service whose state lives in `state_dir`; yields its base URL."""
    service = ControlService(state_dir / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
