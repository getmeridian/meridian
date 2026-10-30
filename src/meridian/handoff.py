"""End-user handoff: the subscription URL is also every person's connection page."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from meridian.cluster import ClusterConfig
from meridian.control_access import access_panel
from meridian.core.clients import PanelUserLike


@dataclass(frozen=True)
class HandoffLinks:
    """What to give one end user: a page to open and a subscription to import."""

    username: str
    subscription_url: str = ""
    page_url: str = ""
    error: str = ""
    display_name: str = ""  # the name as typed (Биба) when it differs from the panel username

    @property
    def name(self) -> str:
        """The person's name for output: as typed, else the username."""
        return self.display_name or self.username


def page_url(cluster: ClusterConfig, user: PanelUserLike) -> str:
    """The service serves a page at the person's subscription URL."""
    return cluster.panel.subscription_url(user.short_uuid) if user.short_uuid else ""


def user_handoff(cluster: ClusterConfig, user: PanelUserLike) -> HandoffLinks:
    intent = cluster.topology_intent
    shown = intent.access.display_names.get(user.username, "") if intent is not None else ""
    url = page_url(cluster, user)
    return HandoffLinks(
        user.username,
        subscription_url=url,
        page_url=url,
        display_name=shown,
        error="" if url else "The subscription path is not configured.",
    )


def client_handoffs(cluster: ClusterConfig, usernames: Iterable[str]) -> list[HandoffLinks]:
    """Read handoff links from service users; no server files or local publication state."""
    names = list(usernames)
    if not names:
        return []
    results: list[HandoffLinks] = []
    with access_panel(cluster) as panel:
        for name in names:
            user = panel.get_user(name)
            results.append(
                user_handoff(cluster, user)
                if user is not None
                else HandoffLinks(
                    name,
                    error="The person does not exist in the control service.",
                )
            )
    return results
