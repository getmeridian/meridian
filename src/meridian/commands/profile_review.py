"""The same private gain/loss review serves profile editing and fleet document import."""

from meridian.cluster import ClusterConfig
from meridian.compiler.access_profiles import validate_profile_backend
from meridian.compiler.errors import TopologyCompileError
from meridian.console import fail
from meridian.control.client import ControlClient
from meridian.core.access_profile_views import AccessProfileReview
from meridian.core.client_limits import UserCreationSettings
from meridian.core.topology import SetupIntent
from meridian.i18n import t


def profile_review_paths(review: AccessProfileReview | None) -> frozenset[str]:
    """Profile identifiers may look like secret keys; their compiled grants remain typed public topology."""
    return (
        frozenset(f"data.access_review.access_profiles.{name}" for name in review.access_profiles)
        if review is not None
        else frozenset()
    )


def require_profile_backend(intent: SetupIntent, *, requested: bool = False) -> None:
    try:
        validate_profile_backend(intent, requested=requested)
    except TopologyCompileError:
        fail(t("profiles.migrate_first"), hint_type="user")


def export_creation_memberships(cluster: ClusterConfig, document: dict) -> None:
    """Carry live declared memberships as creation-only inputs; existing people keep service ownership."""
    if cluster.topology_intent is None or not cluster.topology_intent.access_profiles:
        return
    access = document["access"]
    settings = dict(access.get("creation_settings", {}))
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        for user in client.list_users():
            if user.username not in access["users"]:
                continue
            if user.profiles != ["default"] or "profiles" in settings.get(user.username, {}):
                initial = UserCreationSettings.model_validate(
                    {**settings.get(user.username, {}), "profiles": user.profiles}
                )
                settings[user.username] = initial.to_data()
    if settings:
        access["creation_settings"] = settings
