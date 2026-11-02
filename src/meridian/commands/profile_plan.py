"""Keep the compiled plan available before the control service has bootstrapped."""

from meridian.cluster import ClusterConfig
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.reconciler.resource_executor import inspection_generation
from meridian.reconciler.resources import ResourceInspection, ResourceInspectionResult, build_resource_actions
from meridian.setup.profile_review import ProfileReviewUnavailableError
from meridian.setup.runtime import SetupRuntime


def inspect_profile_plan(
    cluster: ClusterConfig, intent: SetupIntent, runtime: SetupRuntime
) -> ResourceInspectionResult:
    if cluster.panel.url and cluster.panel.api_token:
        return runtime.inspect_intent(intent)
    review = runtime.review(SetupDraft.from_intent(intent), cluster=cluster)
    generation = inspection_generation(review.plan, cluster)
    return ResourceInspectionResult(
        plan_hash=review.plan.plan_hash,
        generation=generation,
        review_hash=review.review_hash,
        retiring=[binding.logical_id for binding in review.retiring],
        inspections=[
            ResourceInspection(action=action, error=str(ProfileReviewUnavailableError()))
            for action in build_resource_actions(review.plan, generation)
        ],
    )
