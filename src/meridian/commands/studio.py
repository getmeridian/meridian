"""Launch executable Meridian Studio on localhost."""

from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

from meridian.console import err_console, fail, info, ok, warn
from meridian.engine.api import create_engine_app
from meridian.engine.assets import resolve_studio_assets

# Browsers keep Studio's draft per origin, so the same port across launches keeps it.
DEFAULT_PORT = 7391
_FALLBACK_PORTS = 9


def run(port: int | None = None, *, assets_dir: str = "", no_open: bool = False) -> None:
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
    session_key = secrets.token_urlsafe(32)
    app = create_engine_app(assets_dir=assets, host=host, port=resolved_port, session_key=session_key)
    url = studio_url(host, resolved_port, session_key)

    info(f"Serving Studio assets from {assets}")
    ok("Meridian Studio is running. Open this address:")
    # soft_wrap keeps the long key on one line, so copying it from the terminal works.
    err_console.print(f"    {url}", soft_wrap=True, markup=False, highlight=False)
    info("The address carries a key that changes on every launch; keep it private")
    info("Press Ctrl+C to stop; a running deploy finishes its current step first")

    if not no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=resolved_port, log_level="warning", access_log=False)


def studio_url(host: str, port: int, session_key: str) -> str:
    """The Studio address with its key in the fragment, which browsers never send to a server."""
    return f"http://{host}:{port}/studio/#k={session_key}"


def _choose_port(host: str, requested: int | None) -> int:
    """An explicit port must be free; by default prefer the stable port, then its neighbours."""
    if requested:
        if not _port_is_free(host, requested):
            fail(
                f"Localhost port {requested} is unavailable",
                hint="Choose another --port, or use --port 0 to select one automatically.",
                hint_type="user",
            )
        return requested
    if requested is None:
        for candidate in range(DEFAULT_PORT, DEFAULT_PORT + _FALLBACK_PORTS + 1):
            if _port_is_free(host, candidate):
                if candidate != DEFAULT_PORT:
                    warn(f"Port {DEFAULT_PORT} is busy; using {candidate}. Drafts saved on {DEFAULT_PORT} stay there")
                return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_is_free(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        return False
    return True
