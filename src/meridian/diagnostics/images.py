"""Read-only observation of the Remnawave images each fleet server runs, against the CLI's pins."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Literal

from meridian.config import (
    REMNAWAVE_BACKEND_IMAGE,
    REMNAWAVE_NODE_IMAGE,
    REMNAWAVE_POSTGRES_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
    REMNAWAVE_VALKEY_IMAGE,
)
from meridian.core.execution import ServerConnection
from meridian.remnawave.versions import ImageStatus, image_status

Component = Literal["backend", "subscription_page", "postgres", "valkey", "node"]


@dataclass(frozen=True)
class ImageComponent:
    """One Meridian-run container and the image the CLI pins for it."""

    component: Component
    container: str
    pinned: str
    data: bool = False
    cross_major: bool = True


CONTROL_COMPONENTS: tuple[ImageComponent, ...] = (
    ImageComponent("backend", "remnawave", REMNAWAVE_BACKEND_IMAGE),
    ImageComponent("subscription_page", "remnawave-subscription-page", REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE),
    ImageComponent("postgres", "remnawave-db", REMNAWAVE_POSTGRES_IMAGE, data=True, cross_major=False),
    ImageComponent("valkey", "remnawave-redis", REMNAWAVE_VALKEY_IMAGE, data=True),
)
NODE_COMPONENTS: tuple[ImageComponent, ...] = (ImageComponent("node", "remnawave-node", REMNAWAVE_NODE_IMAGE),)
CHANGES: frozenset[ImageStatus] = frozenset({"upgrade", "absent"})


@dataclass(frozen=True)
class ImageObservation:
    component: Component
    container: str
    pinned: str
    running: str  # empty when the container is absent
    status: ImageStatus


@dataclass(frozen=True)
class ServerImages:
    """Every observed component on one server; ``error`` set means the evidence is unavailable."""

    server_ref: str
    label: str
    images: tuple[ImageObservation, ...] = ()
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error

    @property
    def changes(self) -> tuple[ImageObservation, ...]:
        return tuple(image for image in self.images if image.status in CHANGES)


_ABSENT = "-"


def observe_images(
    conn: ServerConnection, components: tuple[ImageComponent, ...], *, server_ref: str, label: str
) -> ServerImages:
    """Read each container's configured image with `docker inspect`; never mutates the server."""
    containers = " ".join(shlex.quote(component.container) for component in components)
    command = (
        "command -v docker >/dev/null 2>&1 || exit 127; "
        f"for c in {containers}; do "
        "image=$(docker inspect --format '{{.Config.Image}}' \"$c\" 2>/dev/null) || image=-; "
        'printf \'%s\\t%s\\n\' "$c" "${image:--}"; done'
    )
    try:
        result = conn.run(command, timeout=30)
    except Exception as exc:  # noqa: BLE001 - any transport failure is unavailable evidence
        return ServerImages(server_ref, label, error=str(exc) or type(exc).__name__)
    if result.returncode == 127:
        return ServerImages(server_ref, label, error="docker is not installed or not on PATH")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return ServerImages(server_ref, label, error=detail[-1] if detail else f"exit {result.returncode}")
    running: dict[str, str] = {}
    for line in result.stdout.splitlines():
        container, _, image = line.partition("\t")
        running[container.strip()] = image.strip()
    if any(component.container not in running for component in components):
        return ServerImages(server_ref, label, error="docker inspect returned incomplete output")
    images = []
    for component in components:
        image = running[component.container]
        image = "" if image == _ABSENT else image
        status = image_status(component.pinned, image or None, data=component.data, cross_major=component.cross_major)
        images.append(ImageObservation(component.component, component.container, component.pinned, image, status))
    return ServerImages(server_ref, label, tuple(images))
