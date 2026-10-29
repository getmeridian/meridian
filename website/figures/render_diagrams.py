# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""Render Mermaid diagrams to theme-aware SVG.

Driven by `node scripts/diagrams.mjs render` (run in website/), which passes a JSON list of
`{"id", "source"}` objects on stdin and decides file names. Mermaid comes from the npm registry
tarball pinned below, checked against its SHA-512 and cached (offline, `--mermaid DIR` takes an
unpacked package of the same version); Chromium comes from Playwright
(`uv run --with playwright playwright install chromium`, or `--channel chrome`).

Mermaid paints with sentinel colors, which are then swapped for CSS variables: Starlight's in
the docs, the site tokens on blog pages, and a light fallback. One SVG therefore follows the
reader's theme. Text is measured in Source Sans 3, the font the pages use.
"""

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

MERMAID_VERSION = "11.14.0"
MERMAID_SHA512 = "GSGloRsBs+JINmmhl0JDwjpuezCsHB4WGI4NASHxL3fHo3o/BRXTxhDLKnln8/Q0lRFRyDdEjmk1/d5Sn1Xz8g=="
MERMAID_URL = f"https://registry.npmjs.org/mermaid/-/mermaid-{MERMAID_VERSION}.tgz"
CACHE = Path.home() / ".cache" / "meridian-figures"
WEBSITE = Path(__file__).resolve().parent.parent
FONT = WEBSITE / "public" / "fonts" / "source-sans-3-latin.woff2"

# Sentinel color -> CSS. Docs pages define --sl-color-*, blog pages the tokens.css palette.
PAGE_BG = "var(--sl-color-black, var(--bg, #fdfaf6))"
PALETTE = {
    "#a00001": "var(--sl-color-gray-6, var(--s2, #f5f0e8))",  # node fill
    "#a00002": "var(--sl-color-gray-6, var(--s2, #f5f0e8))",  # secondary fill
    "#a00003": PAGE_BG,  # tertiary fill
    "#a00004": "var(--sl-color-gray-5, var(--b2, #d5cfc3))",  # borders
    "#a00005": "var(--sl-color-white, var(--t1, #1a1d2e))",  # text
    "#a00006": "var(--sl-color-accent, var(--ac, #c8933a))",  # edges and arrowheads
    "#a00007": PAGE_BG,  # edge label background
    "#a00008": PAGE_BG,  # subgraph fill
}
THEME_VARIABLES = {
    "background": "#a00007",
    "primaryColor": "#a00001",
    "mainBkg": "#a00001",
    "secondaryColor": "#a00002",
    "tertiaryColor": "#a00003",
    "primaryBorderColor": "#a00004",
    "secondaryBorderColor": "#a00004",
    "tertiaryBorderColor": "#a00004",
    "nodeBorder": "#a00004",
    "clusterBorder": "#a00004",
    "primaryTextColor": "#a00005",
    "secondaryTextColor": "#a00005",
    "tertiaryTextColor": "#a00005",
    "textColor": "#a00005",
    "nodeTextColor": "#a00005",
    "titleColor": "#a00005",
    "lineColor": "#a00006",
    "defaultLinkColor": "#a00006",
    "arrowheadColor": "#a00006",
    "edgeLabelBackground": "#a00007",
    "clusterBkg": "#a00008",
}
FONT_STACK = "'Source Sans 3', system-ui, sans-serif"
GEOMETRY_ATTRS = set("d points transform x y x1 x2 y1 y2 cx cy r rx ry width height viewBox style".split())

RENDER = """async ([source, id, themeVariables, fontFamily]) => {
  await document.fonts.load(`16px ${fontFamily}`);
  mermaid.initialize({
    startOnLoad: false, theme: 'base', themeVariables, fontFamily, securityLevel: 'strict',
    htmlLabels: false, flowchart: { curve: 'monotoneX', padding: 16, htmlLabels: false },
  });
  return (await mermaid.render(id, source)).svg;
}"""


def mermaid_bundle(package: Path | None) -> str:
    """The pinned mermaid.min.js, downloaded once and verified on every run."""
    if package is not None:
        version = json.loads((package / "package.json").read_text())["version"]
        if version != MERMAID_VERSION:
            sys.exit(f"{package}: mermaid {version}, expected {MERMAID_VERSION}")
        return (package / "dist" / "mermaid.min.js").read_text()
    tarball = CACHE / f"mermaid-{MERMAID_VERSION}.tgz"
    if not tarball.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"downloading {MERMAID_URL}", file=sys.stderr)
        with urllib.request.urlopen(MERMAID_URL, timeout=60) as response:
            tarball.write_bytes(response.read())
    data = tarball.read_bytes()
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != MERMAID_SHA512:
        tarball.unlink()
        sys.exit(f"{MERMAID_URL}: SHA-512 mismatch; refusing to use it")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.extractfile("package/dist/mermaid.min.js")
        if member is None:
            sys.exit("mermaid tarball has no package/dist/mermaid.min.js")
        return member.read().decode()


def round_numbers(value: str) -> str:
    return re.sub(r"-?\d+\.\d{3,}", lambda m: f"{float(m.group()):.2f}".rstrip("0").rstrip("."), value)


def themed(svg: str) -> str:
    """Swap sentinel colors for CSS variables and trim coordinate precision."""
    svg = re.sub(r"rgba\(160, ?0, ?7, ?[\d.]+\)", PALETTE["#a00007"], svg)
    for sentinel, css in PALETTE.items():
        svg = re.sub(re.escape(sentinel), css, svg, flags=re.IGNORECASE)
    leftover = re.findall(r"#a0000\d|rgba?\(160, ?0, ?\d", svg, flags=re.IGNORECASE)
    if leftover:
        sys.exit(f"unmapped sentinel colors: {sorted(set(leftover))}")
    svg = re.sub(
        r'\s([\w:-]+)="([^"]*)"',
        lambda m: f' {m.group(1)}="{round_numbers(m.group(2)) if m.group(1) in GEOMETRY_ATTRS else m.group(2)}"',
        svg,
    )
    # Narrow screens scroll a wide diagram instead of shrinking its text below legibility.
    return re.sub(
        r'style="max-width: ([\d.]+)px;"',
        lambda m: f'style="max-width: {m.group(1)}px; min-width: min({m.group(1)}px, 36rem);"',
        svg,
        count=1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="directory for <id>.svg files")
    parser.add_argument("--channel", help="browser channel, e.g. chrome (default: Playwright's Chromium)")
    parser.add_argument(
        "--mermaid", type=Path, help=f"unpacked mermaid {MERMAID_VERSION} package instead of the download"
    )
    args = parser.parse_args()
    diagrams = json.load(sys.stdin)
    font = "data:font/woff2;base64," + base64.b64encode(FONT.read_bytes()).decode()
    page_html = (
        "<!doctype html><style>@font-face{font-family:'Source Sans 3';font-weight:400 600;"
        f"src:url('{font}') format('woff2')}}body{{font-family:{FONT_STACK}}}</style><body></body>"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel)
        page = browser.new_page()
        page.set_content(page_html)
        page.add_script_tag(content=mermaid_bundle(args.mermaid))
        for diagram in diagrams:
            svg = page.evaluate(RENDER, [diagram["source"], diagram["id"], THEME_VARIABLES, FONT_STACK])
            target = args.out / f"{diagram['id']}.svg"
            target.write_text(themed(svg) + "\n")
            print(f"{target} ({target.stat().st_size // 1024} KB)")
        browser.close()


if __name__ == "__main__":
    main()
