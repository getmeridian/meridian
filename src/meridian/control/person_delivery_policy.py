"""Review and persist one person's private delivery choice without composing node documents."""

from __future__ import annotations

from collections.abc import Callable

from meridian.control.delivery_policy_settings import require_delivery_policy
from meridian.control.delivery_preview import explain_person_delivery
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.core.agent_types import canonical_sha256
from meridian.core.delivery_explain import DeliveryExplainRequest, DeliveryExplanation
from meridian.core.delivery_geodata import GeoData
from meridian.core.delivery_policies import delivery_policy_catalog
from meridian.core.person_delivery_policy import (
    DeliveryPolicyCatalog,
    PersonDeliveryPolicyReview,
    PersonDeliveryPolicyState,
    PersonDeliveryPolicyWrite,
)


class DeliveryPolicyLedger:
    def __init__(
        self,
        store: Store,
        *,
        read_settings: Callable[[], ServiceSettings],
        read_user: Callable[[str], UserRecord],
        read_geodata: Callable[[], GeoData | None],
    ) -> None:
        self.store = store
        self.read_settings = read_settings
        self.read_user = read_user
        self.read_geodata = read_geodata

    def explain(self, request: DeliveryExplainRequest) -> DeliveryExplanation:
        return explain_person_delivery(
            self.store,
            request,
            read_user=self.read_user,
            read_settings=self.read_settings,
            read_geodata=self.read_geodata,
        )

    def catalog(self) -> DeliveryPolicyCatalog:
        with self.store.lock:
            settings = self.read_settings()
            return DeliveryPolicyCatalog(
                policies=delivery_policy_catalog(settings),
                default_policy=settings.default_policy or settings.client_routing,
                profile_policies=settings.profile_policies,
            )

    def person(self, username: str) -> PersonDeliveryPolicyState:
        with self.store.lock:
            user = self.read_user(username)
            return self.state(user, self.read_settings(), user.delivery_policy)

    def state(self, user: UserRecord, settings: ServiceSettings, override: str | None) -> PersonDeliveryPolicyState:
        effective = require_delivery_policy(settings, user.profiles, override)
        return PersonDeliveryPolicyState(
            username=user.username,
            override=override,
            effective=effective,
        )

    def set_person(self, username: str, request: PersonDeliveryPolicyWrite) -> PersonDeliveryPolicyReview:
        request = PersonDeliveryPolicyWrite.model_validate(request.to_data())
        with self.store.lock:
            if not request.confirm:
                return self.review(username, request.policy)
            with self.store.transaction():
                preview = self.review(username, request.policy)
                if preview.review_hash != request.review_hash:
                    raise ServiceError(
                        "stale_delivery_policy",
                        "This person's policy, profiles, or the catalog changed. Preview again.",
                        409,
                    )
                user = self.read_user(username)
                if request.policy is None:
                    self.store.db.execute("DELETE FROM person_delivery_policies WHERE user_id = ?", (user.user_id,))
                elif user.delivery_policy != request.policy:
                    self.store.db.execute(
                        "INSERT INTO person_delivery_policies VALUES (?, ?)"
                        " ON CONFLICT(user_id) DO UPDATE SET policy = excluded.policy",
                        (user.user_id, request.policy),
                    )
                return PersonDeliveryPolicyReview(
                    before=preview.before, after=self.person(username), review_hash=preview.review_hash, confirmed=True
                )

    def review(self, username: str, policy: str | None) -> PersonDeliveryPolicyReview:
        user, settings = self.read_user(username), self.read_settings()
        before = self.state(user, settings, user.delivery_policy)
        after = self.state(user, settings, policy)
        digest = canonical_sha256(
            {
                "user_id": user.user_id,
                "before": before.to_data(),
                "after": after.to_data(),
                "settings": settings.to_data(),
                "profiles": user.profiles,
            }
        )
        return PersonDeliveryPolicyReview(before=before, after=after, review_hash=digest)
