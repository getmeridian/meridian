# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "brotli"]
# ///
"""Rebuild the website's self-hosted font subsets from the upstream releases.

Usage:
    uv run scripts/subset_fonts.py

Downloads each family's official GitHub release, instances the variable font to the
axes the site uses, splits it by script, and writes WOFF2 files to
website/public/fonts/ (licenses in website/public/fonts/licenses/). Keep the ranges
in sync with the unicode-range lines in website/src/styles/fonts.css. Network; never CI.
"""

from __future__ import annotations

import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "website" / "public" / "fonts"

LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2190-2195,U+2212,U+2215,U+FEFF,U+FFFD"
)
CYRILLIC = "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"
ARABIC = (
    "U+0600-06FF,U+0750-077F,U+0870-088E,U+0890-0891,U+0897-08E1,U+08E3-08FF,U+200C-200E,"
    "U+2010-2011,U+204F,U+2E41,U+FB50-FDFF,U+FE70-FEFF"
)
HERO_ITALIC = "U+0020-007E,U+00A0,U+2019"
TEXT_FEATURES = ["kern", "liga", "calt", "ccmp", "locl", "mark", "mkmk", "init", "medi", "fina", "isol", "rlig"]
TEXT_FEATURES += ["pnum", "tnum", "lnum", "case"]
CODE_FEATURES = ["kern", "ccmp", "locl", "mark", "mkmk"]

RELEASES = {
    "fraunces": "https://github.com/undercasetype/Fraunces/releases/download/1.000/UnderCaseType_Fraunces_1.000.zip",
    "source-sans": "https://github.com/adobe-fonts/source-sans/releases/download/3.052R/VF-source-sans-3.052R.zip",
    "source-serif": "https://github.com/adobe-fonts/source-serif/releases/download/4.005R/source-serif-4.005_Desktop.zip",
    "jetbrains-mono": "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip",
    "vazirmatn": "https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip",
}

FR = {"SOFT": 0, "WONK": 0, "wght": (400, 600)}
FR_ITALIC = {"SOFT": 0, "WONK": 0, "wght": 500, "opsz": 72}
SERIF = {"wght": (400, 600), "opsz": 36}
W400_600 = {"wght": (400, 600)}
W400_700 = {"wght": (400, 700)}
SS3 = "SourceSans3VF-Upright.ttf"
SS4 = "SourceSerif4Variable-Roman.ttf"
JBM = "JetBrainsMono[wght].ttf"

# (release, file inside the archive, output name, axis limits, unicode ranges, layout features)
BUILDS = [
    ("fraunces", "Fraunces[SOFT,WONK,opsz,wght].ttf", "fraunces-latin", FR, LATIN, TEXT_FEATURES),
    (
        "fraunces",
        "Fraunces-Italic[SOFT,WONK,opsz,wght].ttf",
        "fraunces-italic-hero",
        FR_ITALIC,
        HERO_ITALIC,
        TEXT_FEATURES,
    ),
    ("source-sans", SS3, "source-sans-3-latin", W400_700, LATIN, TEXT_FEATURES),
    ("source-sans", SS3, "source-sans-3-cyrillic", W400_700, CYRILLIC, TEXT_FEATURES),
    ("source-serif", SS4, "source-serif-4-latin", SERIF, LATIN, TEXT_FEATURES),
    ("source-serif", SS4, "source-serif-4-cyrillic", SERIF, CYRILLIC, TEXT_FEATURES),
    ("jetbrains-mono", JBM, "jetbrains-mono-latin", W400_600, LATIN, CODE_FEATURES),
    ("jetbrains-mono", JBM, "jetbrains-mono-cyrillic", W400_600, CYRILLIC, CODE_FEATURES),
    ("vazirmatn", "fonts/variable/Vazirmatn[wght].ttf", "vazirmatn-arabic", W400_700, ARABIC, TEXT_FEATURES),
]


def codepoints(ranges: str) -> set[int]:
    points: set[int] = set()
    for part in ranges.split(","):
        start, _, end = part.removeprefix("U+").partition("-")
        points.update(range(int(start, 16), int(end or start, 16) + 1))
    return points


def build(source: bytes, out: Path, axes: dict, ranges: str, features: list[str]) -> None:
    font = instancer.instantiateVariableFont(TTFont(io.BytesIO(source)), axes)
    buffer = io.BytesIO()
    font.save(buffer)  # reload eagerly: subsetting a lazily loaded instance fails on gvar
    font = TTFont(io.BytesIO(buffer.getvalue()), lazy=False)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = features
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codepoints(ranges))
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(out)
    print(f"{out.name:34s} {out.stat().st_size:>8,d} bytes")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archives: dict[str, zipfile.ZipFile] = {}
        for name, url in RELEASES.items():
            path = Path(tmp) / f"{name}.zip"
            urllib.request.urlretrieve(url, path)  # noqa: S310 - fixed https release URLs
            archives[name] = zipfile.ZipFile(path)
        for release, member, out_name, axes, ranges, features in BUILDS:
            archive = archives[release]
            entry = next(n for n in archive.namelist() if n.endswith("/" + member) or n == member)
            build(archive.read(entry), OUT / f"{out_name}.woff2", axes, ranges, features)


if __name__ == "__main__":
    main()
