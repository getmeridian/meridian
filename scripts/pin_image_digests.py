"""Resolve Meridian's pinned container images to multi-arch index digests.

Every ``*_IMAGE`` constant in ``meridian.config`` is resolved. Edit the tags
there first (a stale digest suffix is ignored), then run::

    uv run python scripts/pin_image_digests.py

It prints replacement ``config.py`` lines with ``tag@sha256:<index digest>``
(paste them, then run ``ruff format``) after checking that the index carries both
linux/amd64 and linux/arm64.
Extra image references may be passed as arguments to resolve them ad hoc.
Needs network access to Docker Hub; never run in CI.
"""

from __future__ import annotations

import argparse
import sys

from meridian import config
from meridian.remnawave.registry import RegistryError, resolve_index_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve pinned images to multi-arch index digests.")
    parser.add_argument("images", nargs="*", help="extra image references to resolve")
    args = parser.parse_args(argv)
    targets = [(name, value) for name, value in vars(config).items() if name.endswith("_IMAGE")]
    targets += [("", image) for image in args.images]
    failed = False
    for name, image in targets:
        try:
            reference, digest = resolve_index_digest(image)
        except (RegistryError, ValueError, KeyError) as exc:
            print(f"# {name or image}: {exc}", file=sys.stderr)
            failed = True
            continue
        pinned = f"{reference}@{digest}"
        print(f'{name} = (\n    "{pinned}"\n)' if name else pinned)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
