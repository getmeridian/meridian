"""Launch executable Meridian Studio on localhost."""

from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from meridian.console import info, ok, warn
from meridian.engine.api import create_engine_app
from meridian.engine.assets import resolve_studio_assets


def run(port: int = 0, *, assets_dir: str = "", no_open: bool = False) -> None:
    """Serve Studio and the localhost Engine API."""
    host = "127.0.0.1"
    resolved_port = _choose_port(host, port)
    assets = resolve_studio_assets(assets_dir)
    app = create_engine_app(assets_dir=assets, host=host, port=resolved_port)
    url = f"http://{host}:{resolved_port}/studio/"

    if assets:
        info(f"Serving Studio assets from {assets}")
    else:
        warn("Studio assets were not found. Build the website or pass --assets-dir.")
    ok(f"Meridian Studio running at {url}")
    info("Press Ctrl+C to stop")

    if not no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=resolved_port, log_level="warning", access_log=False)


def _choose_port(host: str, requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
