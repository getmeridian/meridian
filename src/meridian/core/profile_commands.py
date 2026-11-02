"""Results for reviewed profile editing and private membership counts."""

from pydantic import Field

from meridian.core.access_profile_views import AccessProfileCatalog, AccessProfileReview
from meridian.core.access_profiles import AccessProfileIntent
from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.models import CoreModel


class ProfileListResult(CoreModel):
    profiles: list[AccessProfileIntent]
    catalog: AccessProfileCatalog


class ProfileEditResult(CoreModel):
    profile: AccessProfileIntent | None = None
    removed: str | None = None
    review: CompiledApplyPreview | None = None
    access_review: AccessProfileReview | None = None
    applied: CompiledApplyResult | None = None
    dry_run: bool = Field(default=False, exclude_if=lambda value: not value)
