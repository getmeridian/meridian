"""Read Docker Hub tags and multi-arch index digests for Remnawave images.

Runs on the deployer's machine (`meridian fleet upgrade`, `scripts/pin_image_digests.py`);
servers only ever pull the resulting ``tag@sha256`` references.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request

from meridian.core.errors import MeridianError
from meridian.remnawave.versions import parse_image, parse_version

REQUIRED_PLATFORMS = frozenset({("linux", "amd64"), ("linux", "arm64")})
_INDEX_MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
_TIMEOUT = 30


class RegistryError(MeridianError):
    """The registry answer cannot be pinned safely."""


def resolve_index_digest(image: str) -> tuple[str, str]:
    """Return ``(repository:tag, sha256:digest)`` for an image's multi-arch index."""
    ref = parse_image(image)
    if not ref.tag:
        raise RegistryError(f"{image}: a tag is required to resolve a digest")
    hub_repo = _hub_repository(ref.repository)
    body, headers = _get(
        f"https://registry-1.docker.io/v2/{hub_repo}/manifests/{urllib.parse.quote(ref.tag)}",
        {"Authorization": f"Bearer {_token(hub_repo)}", "Accept": ", ".join(_INDEX_MEDIA_TYPES)},
    )
    media_type = headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type not in _INDEX_MEDIA_TYPES:
        raise RegistryError(f"{image}: registry returned {media_type or 'no media type'}, not a multi-arch index")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if headers.get("docker-content-digest", digest) != digest:
        raise RegistryError(f"{image}: Docker-Content-Digest does not match the downloaded index")
    platforms = {
        (entry.get("platform", {}).get("os"), entry.get("platform", {}).get("architecture"))
        for entry in json.loads(body).get("manifests", [])
    }
    missing = sorted(f"{os_name}/{arch}" for os_name, arch in REQUIRED_PLATFORMS - platforms)
    if missing:
        raise RegistryError(f"{image}: index lacks {', '.join(missing)}")
    return f"{ref.repository}:{ref.tag}", digest


def latest_release(image: str) -> str:
    """The newest ``X.Y.Z`` release in *image*'s major, as ``repository:tag@sha256:digest``.

    Returns *image* unchanged when nothing newer is published. Pre-release and
    floating tags (``3``, ``latest``, ``3.5.0-rc1``) are never chosen.
    """
    ref = parse_image(image)
    current = parse_version(ref.tag)
    if current is None:
        raise RegistryError(f"{image}: the pinned tag is not an X.Y.Z release")
    hub_repo = _hub_repository(ref.repository)
    body, _ = _get(
        f"https://registry-1.docker.io/v2/{hub_repo}/tags/list?n=10000",
        {"Authorization": f"Bearer {_token(hub_repo)}"},
    )
    releases = [
        (version, tag)
        for tag in json.loads(body).get("tags") or []
        if tag.count(".") == 2 and tag.replace(".", "").isdigit()
        if (version := parse_version(tag)) is not None and version[0] == current[0]
    ]
    newest = max(releases, default=(current, ref.tag))
    if newest[0] <= current:
        return image
    reference, digest = resolve_index_digest(f"{ref.repository}:{newest[1]}")
    return f"{reference}@{digest}"


def _hub_repository(repository: str) -> str:
    host, _, rest = repository.partition("/")
    if rest and ("." in host or ":" in host or host == "localhost"):
        raise RegistryError(f"{repository}: only Docker Hub images are supported")
    return repository if "/" in repository else f"library/{repository}"


def _token(hub_repo: str) -> str:
    query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{hub_repo}:pull"})
    body, _ = _get(f"https://auth.docker.io/token?{query}", {})
    return str(json.loads(body)["token"])


def _get(url: str, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read(), {key.lower(): value for key, value in response.headers.items()}
    except (OSError, ValueError) as exc:
        raise RegistryError(f"Docker Hub request failed: {exc}") from exc
