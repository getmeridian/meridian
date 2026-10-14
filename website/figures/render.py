# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "pillow"]
# ///
"""Render HTML figures to images.

Each `figures/<post>--<name>.html` becomes `src/content/blog/<post>/<name>.webp`;
`figures/img--<name>.html` becomes `public/img/<name>.webp` (README and site images);
`figures/og.html` becomes the link preview cards `public/img/og.jpg` and `og-<locale>.jpg`,
titled with each locale's landing `hero.title`.
Run from the repository root:

    uv run website/figures/render.py            # every figure
    uv run website/figures/render.py 02-proxy   # figures whose file name contains a filter

The first run may need `uv run --with playwright playwright install chromium`.
"""

import io
import re
import sys
from urllib.parse import urlencode
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

FIGURES = Path(__file__).resolve().parent
BLOG = FIGURES.parent / "src" / "content" / "blog"
SITE_IMAGES = FIGURES.parent / "public" / "img"
LANDING_STRINGS = FIGURES.parent / "src" / "i18n" / "landing"
OG_LOCALES = ("en", "ru", "fa", "zh")


def main(filters: list[str]) -> None:
    sources = sorted(path for path in FIGURES.glob("*--*.html") if not filters or any(f in path.name for f in filters))
    og = not filters or any(f in "og.html" for f in filters)
    if not sources and not og:
        sys.exit("No figures matched.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        if og:
            render_og(browser)
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


def hero_title(locale: str) -> str:
    source = (LANDING_STRINGS / f"{locale}.ts").read_text(encoding="utf-8")
    match = re.search(r"'hero\.title':\s*'((?:[^'\\]|\\.)*)'", source)
    if not match:
        sys.exit(f"{locale}.ts: no hero.title")
    return match.group(1).replace("\\'", "'")


def render_og(browser) -> None:
    """Draw each card at 2x and downsample, so hairlines and type stay crisp at 1200x630."""
    page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=2)
    for locale in OG_LOCALES:
        page.goto(f"{(FIGURES / 'og.html').as_uri()}?{urlencode({'lang': locale, 'title': hero_title(locale)})}")
        page.evaluate("document.fonts.ready")
        png = page.locator(".fig").screenshot()
        card = Image.open(io.BytesIO(png)).convert("RGB").resize((1200, 630), Image.LANCZOS)
        target = SITE_IMAGES / ("og.jpg" if locale == "en" else f"og-{locale}.jpg")
        card.save(target, "JPEG", quality=86, optimize=True, progressive=True)
        print(f"og.html ({locale}) -> {target.relative_to(FIGURES.parent)} ({target.stat().st_size // 1024} KB)")
    page.close()


if __name__ == "__main__":
    main(sys.argv[1:])
