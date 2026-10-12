"""Dev tools — preview, debug, and inspect Meridian internals."""

from __future__ import annotations

import http.server
import shutil
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from meridian.console import info, ok, warn
from meridian.models import ProtocolURL
from meridian.pwa import generate_client_files, load_pwa_static_assets
from meridian.urls import generate_qr_base64

# Demo data using RFC 5737 IPs (safe for public display)
DEMO_IP = "198.51.100.1"
DEMO_UUID = "550e8400-e29b-41d4-a716-446655440000"
DEMO_SNI = "www.cloudflare.com"
DEMO_XHTTP_PATH = "xhttp-demo-path"

# Source directories to watch for changes
_SRC_ROOT = Path(__file__).parent.parent  # src/meridian/
_WATCH_DIRS = [
    _SRC_ROOT / "templates" / "pwa",
]


def _build_demo_urls(
    server_ip: str = DEMO_IP,
    reality_uuid: str = DEMO_UUID,
    sni: str = DEMO_SNI,
    *,
    xhttp: bool = True,
) -> list[ProtocolURL]:
    """Build demo VLESS protocol URLs for preview."""
    urls: list[ProtocolURL] = []

    # Reality
    reality_url = (
        f"vless://{reality_uuid}@{server_ip}:443"
        f"?security=reality&encryption=none&type=tcp&flow=xtls-rprx-vision"
        f"&sni={sni}&fp=chrome&pbk=demo-public-key"
        f"#Meridian-Reality"
    )
    urls.append(
        ProtocolURL(
            key="reality",
            label="Primary",
            url=reality_url,
            qr_b64=generate_qr_base64(reality_url),
        )
    )

    # XHTTP
    if xhttp:
        xhttp_url = (
            f"vless://{reality_uuid}@{server_ip}:443"
            f"?security=tls&encryption=none&sni={server_ip}&fp=chrome"
            f"&type=xhttp&path=/{DEMO_XHTTP_PATH}"
            f"#Meridian-XHTTP"
        )
        urls.append(
            ProtocolURL(
                key="xhttp",
                label="XHTTP",
                url=xhttp_url,
                qr_b64=generate_qr_base64(xhttp_url),
            )
        )

    return urls


def _write_mock_stats(output_dir: Path, client_uuid: str) -> None:
    """Write a mock stats JSON file for the preview."""
    import json

    stats_dir = output_dir / "stats"
    stats_dir.mkdir(exist_ok=True)
    stats = {
        "up": 1_234_567_890,
        "down": 9_876_543_210,
        "lastOnline": int(time.time() * 1000) - 300_000,  # 5 min ago
    }
    (stats_dir / f"{client_uuid}.json").write_text(json.dumps(stats))


def _write_preview_files(
    output_dir: Path,
    client_uuid: str,
    server_ip: str,
    client_name: str,
    *,
    watch: bool = False,
    disable_sw: bool = False,
) -> None:
    """Generate and write all preview files to the output directory."""
    protocol_urls = _build_demo_urls(server_ip=server_ip)

    client_files = generate_client_files(
        protocol_urls,
        server_ip=server_ip,
        client_name=client_name,
    )

    static_assets = load_pwa_static_assets()

    neuter_sw = watch or disable_sw

    # Write shared PWA assets
    pwa_dir = output_dir / "pwa"
    pwa_dir.mkdir(exist_ok=True)
    for name, content in static_assets.items():
        if neuter_sw and name == "sw.js":
            # Disable SW to prevent caching stale files / polluting browsers
            (pwa_dir / name).write_text(_NOOP_SW)
        else:
            (pwa_dir / name).write_bytes(content)

    # Write per-client files
    client_dir = output_dir / client_uuid
    client_dir.mkdir(exist_ok=True)
    for name, text in client_files.items():
        if watch and name == "index.html":
            # Inject live-reload script into HTML
            text = text.replace("</body>", _LIVE_RELOAD_SCRIPT + "</body>")
        (client_dir / name).write_text(text)

    _write_mock_stats(output_dir, client_uuid)

    # Write redirect index.html at root for static hosting
    if disable_sw:
        (output_dir / "index.html").write_text(_REDIRECT_HTML.format(uuid=client_uuid))


# No-op service worker — used in watch mode and static output to prevent
# caching stale files or polluting visitors' browsers.
_NOOP_SW = "/* SW disabled */\nself.addEventListener('install',()=>self.skipWaiting());\n"

# Redirect page for static output — placed at root of output directory
# so that /demo/ redirects instantly to /demo/{uuid}/.
_REDIRECT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0;url=./{uuid}/">
<script>location.replace('./{uuid}/')</script>
<title>Meridian Demo</title>
</head>
<body></body>
</html>
"""

# Inline live-reload script — polls /__reload endpoint
_LIVE_RELOAD_SCRIPT = """<script>
(function(){var v='';setInterval(function(){
  fetch('/__reload').then(function(r){return r.text()}).then(function(t){
    if(v&&t!==v)location.reload();v=t;
  }).catch(function(){});
},800)})();
</script>
"""


def _get_source_mtime() -> str:
    """Get latest mtime across watched source files as a string."""
    latest = 0.0
    for d in _WATCH_DIRS:
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file():
                    latest = max(latest, f.stat().st_mtime)
    return str(latest)


def run_preview(
    port: int = 8787,
    client_name: str = "demo",
    server_ip: str = DEMO_IP,
    *,
    no_open: bool = False,
    output: str = "",
    watch: bool = False,
) -> None:
    """Generate PWA connection page and serve locally for testing.

    Uses demo data with RFC 5737 IPs. All PWA features work on localhost
    (service worker, install prompt, offline caching).
    """
    client_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-preview-{client_name}"))

    # Determine output directory
    if output:
        output_dir = Path(output)
        output_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="meridian-preview-"))
        cleanup = True

    try:
        is_static = bool(output) and not watch
        _write_preview_files(
            output_dir,
            client_uuid,
            server_ip,
            client_name,
            watch=watch,
            disable_sw=is_static,
        )

        info(f"PWA files written to {output_dir}")

        if output and not watch:
            ok(f"Preview files saved to {output_dir}")
            info(f"Serve with: python -m http.server {port} -d {output_dir}")
            return

        # Track source mtime for reload detection
        last_mtime = _get_source_mtime()

        # Custom handler with reload endpoint and watch-mode regeneration
        class PreviewHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["directory"] = str(output_dir)
                super().__init__(*args, **kwargs)

            def do_GET(self) -> None:  # noqa: N802
                nonlocal last_mtime
                if self.path == "/__reload":
                    if watch:
                        current = _get_source_mtime()
                        if current != last_mtime:
                            last_mtime = current
                            # Reimport to pick up source changes
                            import importlib

                            import meridian.pwa
                            import meridian.render

                            importlib.reload(meridian.render)
                            importlib.reload(meridian.pwa)
                            from meridian.pwa import generate_client_files as gcf
                            from meridian.pwa import load_pwa_static_assets as lpsa

                            # Monkey-patch for this regeneration
                            _regen(output_dir, client_uuid, server_ip, client_name, gcf, lpsa)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(last_mtime.encode())
                    return
                super().do_GET()

            def log_message(self, *_args: object) -> None:
                pass  # suppress access logs

        server = http.server.HTTPServer(("localhost", port), PreviewHandler)

        page_url = f"http://localhost:{port}/{client_uuid}/"
        ok(f"Preview server running at {page_url}")
        if watch:
            info(f"Watching {_WATCH_DIRS[0]} for changes (live reload)")
        info("Press Ctrl+C to stop")

        if not no_open:
            threading.Timer(0.5, lambda: webbrowser.open(page_url)).start()

        server.serve_forever()

    except KeyboardInterrupt:
        info("Preview server stopped")
    finally:
        if cleanup and output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)


def _regen(
    output_dir: Path,
    client_uuid: str,
    server_ip: str,
    client_name: str,
    generate_fn: object,
    load_fn: object,
) -> None:
    """Regenerate preview files after source change (watch mode)."""
    try:
        protocol_urls = _build_demo_urls(server_ip=server_ip)
        client_files = generate_fn(  # type: ignore[operator]
            protocol_urls,
            server_ip=server_ip,
            client_name=client_name,
        )
        static_assets = load_fn()  # type: ignore[operator]

        pwa_dir = output_dir / "pwa"
        for name, content in static_assets.items():
            if name == "sw.js":
                (pwa_dir / name).write_text(_NOOP_SW)
            else:
                (pwa_dir / name).write_bytes(content)

        client_dir = output_dir / client_uuid
        for name, content in client_files.items():
            if name == "index.html":
                content = content.replace("</body>", _LIVE_RELOAD_SCRIPT + "</body>")
            (client_dir / name).write_text(content)

        info("Regenerated preview files")
    except Exception as exc:
        warn(f"Regeneration failed: {exc}")
