"""Read GitHub releases and the sha256 GitHub publishes for each release asset.

Runs on the deployer's machine (`meridian fleet upgrade`, `scripts/bump_pins.py`);
servers only ever download the resulting version and check its pinned digest.
"""

from __future__ import annotations

import os

import httpx

from meridian.binary_pins import BINARY_SOURCES, BinaryPin, BinarySource, parse_version
from meridian.core.errors import MeridianError

_TIMEOUT = 30


class ReleaseError(MeridianError):
    """The release answer cannot be pinned safely."""


def latest_binary_release(name: str, current: BinaryPin) -> BinaryPin:
    """The newest X.Y.Z release in *current*'s major, with a digest for every asset *current* pins.

    Returns *current* unchanged when nothing newer is published. Drafts, tags
    with suffixes (``v2.9.2-2``), and pre-releases (unless the source ships
    only those) are never chosen.
    """
    source = BINARY_SOURCES[name]
    release = newest_release(source, current.version)
    if release is None:
        return current
    version, body = release
    return BinaryPin(version, _asset_digests(name, source, body, current))


def newest_release(source: BinarySource, version: str) -> tuple[str, dict[str, object]] | None:
    """The newest release above *version* in its major, as ``(X.Y.Z, release)``; None when there is none."""
    pinned = parse_version(version)
    if pinned is None:
        raise ReleaseError(f"{source.repository} {version}: the pinned version is not an X.Y.Z release")
    newest: tuple[tuple[int, int, int], dict[str, object]] | None = None
    for release in _releases(source):
        found = _release_version(source, release)
        if found is None or found[0] != pinned[0] or found <= pinned:
            continue
        if newest is None or found > newest[0]:
            newest = (found, release)
    return None if newest is None else (".".join(map(str, newest[0])), newest[1])


def _release_version(source: BinarySource, release: dict[str, object]) -> tuple[int, int, int] | None:
    tag = release.get("tag_name")
    if release.get("draft") or not isinstance(tag, str) or not tag.startswith(source.tag_prefix):
        return None
    if release.get("prerelease") and not source.prereleases:
        return None
    bare = tag[len(source.tag_prefix) :]
    if bare.count(".") != 2 or not bare.replace(".", "").isdigit():
        return None
    return parse_version(bare)


def _asset_digests(name: str, source: BinarySource, release: dict[str, object], current: BinaryPin) -> dict[str, str]:
    assets = release.get("assets")
    published = {
        str(asset.get("name")): str(asset.get("digest") or "")
        for asset in (assets if isinstance(assets, list) else [])
        if isinstance(asset, dict)
    }
    digests: dict[str, str] = {}
    for key in current.sha256:
        digest = published.get(source.asset_name(key), "")
        if not digest.startswith("sha256:"):
            raise ReleaseError(
                f"{name} {release.get('tag_name')}: GitHub publishes no sha256 for {source.asset_name(key)}"
            )
        digests[key] = digest.removeprefix("sha256:")
    return digests


def _releases(source: BinarySource) -> list[dict[str, object]]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"  # 60 unauthenticated requests an hour is enough otherwise
    try:
        # httpx carries certifi's CA bundle; urllib depends on how Python was installed.
        response = httpx.get(
            f"https://api.github.com/repos/{source.repository}/releases",
            params={"per_page": 50},
            headers=headers,
            timeout=_TIMEOUT,
        )
        if response.status_code in (403, 429) and not token:
            raise ReleaseError(
                f"GitHub refused the release list for {source.repository} (rate limit)",
                hint="Set GITHUB_TOKEN (for example `export GITHUB_TOKEN=$(gh auth token)`) and retry.",
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ReleaseError(f"GitHub request for {source.repository} failed: {exc}") from exc
    if not isinstance(body, list):
        raise ReleaseError(f"GitHub returned no release list for {source.repository}")
    return [release for release in body if isinstance(release, dict)]
