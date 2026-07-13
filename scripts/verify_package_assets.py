"""Verify that a built Meridian wheel contains the generated Studio site."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile

_REQUIRED_ASSETS = {
    "meridian/studio_assets/studio/index.html",
}


def verify_wheel_assets(wheel: Path) -> None:
    """Raise ValueError when required Studio assets are absent."""
    if not wheel.is_file():
        raise ValueError(f"Wheel does not exist: {wheel}")

    with ZipFile(wheel) as archive:
        missing = sorted(_REQUIRED_ASSETS - set(archive.namelist()))
    if missing:
        raise ValueError(f"Wheel is missing Studio assets: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", type=Path, nargs="+")
    args = parser.parse_args()
    for wheel in args.wheels:
        try:
            verify_wheel_assets(wheel)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"OK: Studio assets bundled in {wheel}")  # noqa: T201


if __name__ == "__main__":
    main()
