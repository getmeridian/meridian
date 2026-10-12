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
import hashlib
import json
import sys
import urllib.parse
import urllib.request

from meridian import config
from meridian.remnawave.versions import parse_image

REQUIRED_PLATFORMS = frozenset({("linux", "amd64"), ("linux", "arm64")})
_INDEX_MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
_TIMEOUT = 30


class DigestError(RuntimeError):
    """The registry answer cannot be pinned safely."""


def _hub_repository(repository: str) -> str:
    host, _, rest = repository.partition("/")
    if rest and ("." in host or ":" in host or host == "localhost"):
        raise DigestError(f"{repository}: only Docker Hub images are supported")
    return repository if "/" in repository else f"library/{repository}"


def _get(url: str, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read(), {key.lower(): value for key, value in response.headers.items()}


def resolve_index_digest(image: str) -> tuple[str, str]:
    """Return ``(repository:tag, sha256:digest)`` for an image's multi-arch index."""
    ref = parse_image(image)
    if not ref.tag:
        raise DigestError(f"{image}: a tag is required to resolve a digest")
    hub_repo = _hub_repository(ref.repository)
    token_query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{hub_repo}:pull"})
    token_body, _ = _get(f"https://auth.docker.io/token?{token_query}", {})
    token = json.loads(token_body)["token"]
    body, headers = _get(
        f"https://registry-1.docker.io/v2/{hub_repo}/manifests/{urllib.parse.quote(ref.tag)}",
        {"Authorization": f"Bearer {token}", "Accept": ", ".join(_INDEX_MEDIA_TYPES)},
    )
    media_type = headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type not in _INDEX_MEDIA_TYPES:
        raise DigestError(f"{image}: registry returned {media_type or 'no media type'}, not a multi-arch index")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if headers.get("docker-content-digest", digest) != digest:
        raise DigestError(f"{image}: Docker-Content-Digest does not match the downloaded index")
    platforms = {
        (entry.get("platform", {}).get("os"), entry.get("platform", {}).get("architecture"))
        for entry in json.loads(body).get("manifests", [])
    }
    missing = sorted(f"{os_name}/{arch}" for os_name, arch in REQUIRED_PLATFORMS - platforms)
    if missing:
        raise DigestError(f"{image}: index lacks {', '.join(missing)}")
    return f"{ref.repository}:{ref.tag}", digest


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
        except (DigestError, OSError, ValueError, KeyError) as exc:
            print(f"# {name or image}: {exc}", file=sys.stderr)
            failed = True
            continue
        pinned = f"{reference}@{digest}"
        print(f'{name} = (\n    "{pinned}"\n)' if name else pinned)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
