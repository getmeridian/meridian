"""Screenshot the demo connection page as an iPhone sees it, for the phone scene.

    uv run --with playwright python video/recording/phone.py

Builds `meridian dev preview` with demo data (an RFC 5737 address, so nothing
real reaches the video), serves it on localhost, and saves full-page shots to
video/public/phone/ with the local Chrome.
"""

from __future__ import annotations

import functools
import http.server
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "video" / "public" / "phone"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.5 Mobile/15E148 Safari/604.1"
)
SHOTS = {"page-qr": True}  # name -> QR code open


def main(name: str = "Mom") -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as site:
        subprocess.run(
            [ROOT / ".venv" / "bin" / "meridian", "dev", "preview", "-o", site, "--name", name, "--ip", "198.51.100.7"],
            check=True,
            capture_output=True,
        )
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome")
            for shot, open_qr in SHOTS.items():
                context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    device_scale_factor=3,
                    is_mobile=True,
                    has_touch=True,
                    color_scheme="dark",
                    user_agent=IPHONE,
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(800)
                if open_qr:
                    page.get_by_text("Show QR code").first.click()
                    page.wait_for_timeout(400)
                page.screenshot(path=OUT / f"{shot}.png", full_page=True)
                context.close()
            browser.close()
        server.shutdown()
    print("\n".join(str(path.relative_to(ROOT)) for path in sorted(OUT.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
