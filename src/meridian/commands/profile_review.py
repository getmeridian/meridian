"""The same private gain/loss review serves profile editing and fleet document import."""

from meridian.cluster import ClusterConfig
from meridian.compiler.access_profiles import validate_profile_backend
from meridian.compiler.errors import TopologyCompileError
from meridian.console import fail
from meridian.core.access_profile_views import AccessProfileReview
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
    """People seeds in fleet exports are a separate phase; topology contains no people."""
