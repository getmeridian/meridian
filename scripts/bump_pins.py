"""Raise Meridian's default pins to the newest release in each tested major.

    uv run python scripts/bump_pins.py                         # report only
    uv run python scripts/bump_pins.py --write --summary pr.md

Realm and RealiTLScanner take the sha256 GitHub publishes for each release
asset; acme.sh publishes no assets, so its tagged script is hashed here. The
Remnawave backend, node, and subscription page move to the newest X.Y.Z tag in
their major, pinned by multi-arch index digest. Xray is left alone: its pin
follows the Xray bundled in the pinned node image.

Network access; the weekly `bump-pins` workflow runs it and opens a pull
request for review, never as part of CI checks. Exits 1 when any source could
not be checked, after writing whatever could.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from meridian import config
from meridian.binary_pins import BINARY_SOURCES, BinaryPin
from meridian.github_releases import ReleaseError, latest_binary_release, newest_release
from meridian.remnawave.registry import RegistryError, latest_release
from meridian.remnawave.versions import UPGRADABLE_PINS, parse_image

CONFIG = Path(__file__).resolve().parents[1] / "src" / "meridian" / "config.py"


@dataclass(frozen=True)
class Bump:
    component: str
    old: str
    new: str
    replacements: tuple[tuple[str, str], ...]  # exact config.py substrings, old -> new


def binary_bump(component: str, name: str, version_constant: str, current: BinaryPin) -> Bump | None:
    pin = latest_binary_release(name, current)
    if pin.version == current.version:
        return None
    replacements = [(f'{version_constant} = "{current.version}"', f'{version_constant} = "{pin.version}"')]
    replacements += [(current.sha256[key], pin.sha256[key]) for key in current.sha256]
    return Bump(component, current.version, pin.version, tuple(replacements))


def acme_bump() -> Bump | None:
    release = newest_release(BINARY_SOURCES["acme.sh"], config.ACME_SH_VERSION)
    if release is None:
        return None
    version = release[0]
    url = f"https://raw.githubusercontent.com/acmesh-official/acme.sh/{version}/acme.sh"
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ReleaseError(f"acme.sh {version}: download failed: {exc}") from exc
    digest = hashlib.sha256(response.content).hexdigest()
    return Bump(
        "acme.sh",
        config.ACME_SH_VERSION,
        version,
        (
            (f'ACME_SH_VERSION = "{config.ACME_SH_VERSION}"', f'ACME_SH_VERSION = "{version}"'),
            (config.ACME_SH_SHA256, digest),
        ),
    )


def image_bump(name: str, image: str) -> Bump | None:
    newer = latest_release(image)
    if newer == image:
        return None
    return Bump(parse_image(image).repository, parse_image(image).tag, parse_image(newer).tag, ((image, newer),))


def checks() -> list[tuple[str, object]]:
    scanner = BinaryPin(config.REALITL_SCANNER_VERSION, dict(config.REALITL_SCANNER_ASSETS.values()))
    realm = BinaryPin(config.REALM_VERSION, dict(config.REALM_SHA256))
    items: list[tuple[str, object]] = [
        ("Realm", lambda: binary_bump("Realm", "realm", "REALM_VERSION", realm)),
        ("RealiTLScanner", lambda: binary_bump("RealiTLScanner", "realitlscanner", "REALITL_SCANNER_VERSION", scanner)),
        ("acme.sh", acme_bump),
    ]
    items += [(name, lambda name=name, image=image: image_bump(name, image)) for name, image in UPGRADABLE_PINS.items()]
    return items


def summary(bumps: list[Bump], failures: list[str]) -> str:
    lines = ["Weekly pin bump from `scripts/bump_pins.py`.", ""]
    if bumps:
        lines += ["| Component | From | To |", "|---|---|---|"]
        lines += [f"| {bump.component} | {bump.old} | {bump.new} |" for bump in bumps]
        lines += ["", "Before merging:", ""]
        if any("/" in bump.component for bump in bumps):
            lines += [
                "- [ ] `make system-lab` passes (Remnawave moved)",
                "- [ ] the CHANGELOG version matrix names the new images",
                "- [ ] `XRAY_VERSION` and `XRAY_SHA256` match the Xray bundled in the new node image",
                "- [ ] `uv run python scripts/export_geodata_lists.py` rerun for the new node image",
            ]
        lines += ["- [ ] CHANGELOG mentions the bump"]
    if failures:
        lines += ["", "Could not check:", "", *(f"- {failure}" for failure in failures)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raise default pins to the newest same-major releases.")
    parser.add_argument("--write", action="store_true", help="rewrite src/meridian/config.py")
    parser.add_argument("--summary", type=Path, help="write a Markdown summary (pull request body) here")
    args = parser.parse_args(argv)

    bumps: list[Bump] = []
    failures: list[str] = []
    for label, check in checks():
        try:
            bump = check()  # type: ignore[operator]
        except (ReleaseError, RegistryError) as exc:
            failures.append(f"{label}: {exc}")
            print(f"# {label}: {exc}", file=sys.stderr)
            continue
        if bump is not None:
            bumps.append(bump)
            print(f"{bump.component}: {bump.old} -> {bump.new}")

    if args.write and bumps:
        text = CONFIG.read_text(encoding="utf-8")
        for bump in bumps:
            for old, new in bump.replacements:
                if text.count(old) != 1:
                    raise SystemExit(f"{bump.component}: expected exactly one {old!r} in config.py")
                text = text.replace(old, new)
        CONFIG.write_text(text, encoding="utf-8")
    if args.summary:
        args.summary.write_text(summary(bumps, failures), encoding="utf-8")
    if not bumps and not failures:
        print("Every pin is already the newest release in its major.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
