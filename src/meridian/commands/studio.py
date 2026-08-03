"""Launch executable Meridian Studio on localhost."""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

from meridian.console import fail, info, ok
from meridian.engine.api import create_engine_app
from meridian.engine.assets import resolve_studio_assets


def run(port: int = 0, *, assets_dir: str = "", no_open: bool = False) -> None:
    """Serve Studio and the localhost Engine API."""
    host = "127.0.0.1"
    resolved_port = _choose_port(host, port)
    if assets_dir:
        explicit_assets = Path(assets_dir).expanduser()
        if not explicit_assets.is_dir() or not (explicit_assets / "studio" / "index.html").is_file():
            fail(
                f"Studio assets were not found in {explicit_assets}",
                hint="Pass the website build output containing studio/index.html.",
                hint_type="user",
            )
    assets = resolve_studio_assets(assets_dir)
    if assets is None:
        fail(
            "Studio assets are not installed",
            hint="Reinstall Meridian with Studio assets, or pass --assets-dir after building the website.",
            hint_type="system",
        )
    app = create_engine_app(assets_dir=assets, host=host, port=resolved_port)
    url = f"http://{host}:{resolved_port}/studio/"

    info(f"Serving Studio assets from {assets}")
    ok(f"Meridian Studio running at {url}")
    info("Press Ctrl+C to stop")

    if not no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=resolved_port, log_level="warning", access_log=False)


def _choose_port(host: str, requested: int) -> int:
    if requested:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, requested))
        except OSError:
            fail(
                f"Localhost port {requested} is unavailable",
                hint="Choose another --port, or use --port 0 to select one automatically.",
                hint_type="user",
            )
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
