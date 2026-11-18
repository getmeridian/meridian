"""Screenshot both website demos into website/public/img/showcase/ (local only, never in CI).

    uv run python scripts/build_showcase.py /tmp/meridian-demo
    uv run --with playwright --with pillow python scripts/showcase_screenshots.py /tmp/meridian-demo

Needs `playwright install chromium` once. Pages are served from the export over loopback, under
/demo/ as on the website, so nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import io
import tempfile
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, ViewportSize, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "website/public/img/showcase"
WIDTH = 1600  # Wide enough for a README column and a retina landing card.
QUALITY = 80
# A fixed zone and locale keep dates identical on every machine.
ZONE = "UTC"
LOCALE = "en-US"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the stdlib's name
        pass


def save(page: Page, name: str, *, width: int = WIDTH) -> None:
    image: Image.Image = Image.open(io.BytesIO(page.screenshot()))
    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
    target = OUTPUT / f"{name}.webp"
    image.save(target, "WEBP", quality=QUALITY, method=6)
    print(f"{target.relative_to(ROOT)}  {image.width}x{image.height}  {target.stat().st_size // 1024} KB")


def settle(page: Page, text: str, *, scroll_to: str | None = None) -> None:
    page.get_by_text(text).filter(visible=True).first.wait_for()
    # The banner belongs to the live demo, not to product screenshots.
    page.add_style_tag(content="#demo-banner { display: none !important; }")
    if scroll_to:
        # Start just above the element's own edge (below a sticky header), so no neighbour shows cut in half.
        page.locator(scroll_to).first.evaluate(
            "e => { const h = document.querySelector('header');"
            " const pinned = h && ['sticky', 'fixed'].includes(getComputedStyle(h).position);"
            " const top = pinned ? h.getBoundingClientRect().bottom : 0;"
            " window.scrollTo(0, e.getBoundingClientRect().top + scrollY - top - 12); }"
        )
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(400)  # Let the usage bars finish drawing.


def console_shots(page: Page, base: str, *, landing_only: bool) -> None:
    page.goto(base + "console/?theme=light#/people")
    settle(page, "Luma")
    save(page, "console-people")
    if landing_only:
        return
    page.goto(base + "console/?theme=light#/people/Luma")
    settle(page, "Access & limits")
    save(page, "console-person")
    page.goto(base + "console/?theme=light#/servers")
    settle(page, "Runtime healthy")
    save(page, "console-servers")
    page.goto(base + "console/?theme=light#/settings/privacy")
    settle(page, "Client IP")
    save(page, "console-settings")


def run(export: Path, *, landing_only: bool = False) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as site:
        (Path(site) / "demo").symlink_to(export)
        handler = functools.partial(QuietHandler, directory=site)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}/demo/"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                desktop: ViewportSize = {"width": 1280, "height": 860}
                light = browser.new_context(
                    viewport=desktop, device_scale_factor=2, color_scheme="light", timezone_id=ZONE, locale=LOCALE
                )
                console_shots(light.new_page(), base, landing_only=landing_only)
                dark = browser.new_context(
                    viewport=desktop, device_scale_factor=2, color_scheme="dark", timezone_id=ZONE, locale=LOCALE
                )
                page = dark.new_page()
                page.goto(base + "console/?theme=dark#/people")
                settle(page, "Luma")
                save(page, "console-people-dark")
                iphone = playwright.devices["iPhone 14"]
                phone = browser.new_context(
                    viewport=iphone["viewport"],
                    device_scale_factor=iphone["device_scale_factor"],
                    is_mobile=True,
                    has_touch=True,
                    user_agent=iphone["user_agent"],
                    color_scheme="light",
                    timezone_id=ZONE,
                    locale=LOCALE,
                )
                page = phone.new_page()
                page.goto(base + "?lang=en")
                page.locator('#account-panel:not([hidden]):not([aria-busy="true"])').wait_for()
                page.locator("a[data-import]").first.wait_for()
                settle(page, "Happ")
                if not landing_only:
                    save(page, "connection-page-account", width=780)
                settle(page, "Happ", scroll_to="#setup-card")
                save(page, "connection-page-phone", width=780)
                browser.close()
        finally:
            server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="a directory written by scripts/build_showcase.py")
    parser.add_argument(
        "--landing-only", action="store_true", help="refresh only the three images used on the landing page"
    )
    args = parser.parse_args()
    run(args.export.resolve(), landing_only=args.landing_only)


if __name__ == "__main__":
    main()
