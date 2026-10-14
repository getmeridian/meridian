# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "pillow"]
# ///
"""Render HTML figures to images.

Each `figures/<post>--<name>.html` becomes `src/content/blog/<post>/<name>.webp`;
`figures/img--<name>.html` becomes `public/img/<name>.webp` (README and site images).
Run from the repository root:

    uv run website/figures/render.py            # every figure
    uv run website/figures/render.py 02-proxy   # figures whose file name contains a filter

The first run may need `uv run --with playwright playwright install chromium`.
"""

import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

FIGURES = Path(__file__).resolve().parent
BLOG = FIGURES.parent / "src" / "content" / "blog"
SITE_IMAGES = FIGURES.parent / "public" / "img"


def main(filters: list[str]) -> None:
    sources = sorted(path for path in FIGURES.glob("*--*.html") if not filters or any(f in path.name for f in filters))
    if not sources:
        sys.exit("No figures matched.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=2)
        for source in sources:
            post, name = source.stem.split("--", 1)
            folder = SITE_IMAGES if post == "img" else BLOG / post
            if not folder.is_dir():
                sys.exit(f"{source.name}: no blog post folder {post}")
            page.goto(source.as_uri())
            page.evaluate("document.fonts.ready")
            png = page.locator(".fig").screenshot()
            target = folder / f"{name}.webp"
            Image.open(io.BytesIO(png)).save(target, "WEBP", quality=90, method=6)
            print(f"{source.name} -> {target.relative_to(FIGURES.parent)} ({target.stat().st_size // 1024} KB)")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
