"""Service-owned evidence and templates for the website's interactive API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations
from typing import Any, Protocol
from urllib.parse import urlencode

from meridian.control.models import USERNAME_PATTERN, UserUpdate
from meridian.control.service import ControlService
from meridian.core.client_limits import MAX_TRAFFIC_LIMIT_BYTES
from meridian.core.delivery_explain import DeliveryExplainRequest
from meridian.core.people_bulk import BULK_WRITABLE_FIELDS, BulkAction, BulkFields, BulkPreviewRequest
from meridian.core.people_query import PeoplePerson
from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
from meridian.core.person_metadata import MAX_NOTE_CHARACTERS
from meridian.core.response_rule_test import ResponseRuleTestRequest
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from meridian.core.topology import PROTOCOL_PRIORITY
from scripts.showcase_seed import PAGE_PERSON, PROFILES


class Recorder(Protocol):
    def __call__(self, path: str, method: str = "GET", body: Any = None) -> Any: ...


# These inputs are sent to the service; the browser reuses its decisions and format evidence.
FORMAT_INPUTS = {
    "default": {"user_agent": ""},
    "accept": {"accept": "text/html"},
    "mihomo": {"user_agent": "Mihomo/1"},
    "stash": {"user_agent": "Stash/1"},
    "singbox": {"user_agent": "SFA/1"},
    "clash": {"user_agent": "Clash/1"},
    "xray-json": {"user_agent": "v2rayNG/1.2.3"},
    "demo-clash": {"user_agent": "DemoClash/1"},
    "demo-blocked": {"user_agent": "DemoBlocked/1"},
}
SUFFIXES = ("", "info", "json", "v2ray-json", "mihomo", "clash", "stash", "singbox")


def record_people_queries(record: Recorder) -> None:
    """Cover each predicate, contextual facets, missing-date sorts, and cursor pages."""
    record("api/people")
    queries: list[Mapping[str, str | int]] = [
        *(dict(q=value) for value in (" Lu ", "a", "NoMatch", "%", "_")),
        *(dict(status=value) for value in ("active", "disabled", "limited", "expired")),
        *(dict(tag=value) for value in ("none", "FAMILY", "TRIAL", "FRIENDS")),
        *(dict(profile=value) for value in ("default", "nearby", "backup", "unknown")),
        *(dict(expires=value) for value in (1, 7, 30, 365)),
        *(dict(quota=value) for value in (1, 50, 90, 100)),
        {"never": 1},
        *(dict(attention=value) for value in ("all", "expiring", "quota", "unused", "stopped")),
        *(dict(lifecycle=value) for value in ("pending", "renewing", "fixed", "unlimited")),
        *(
            dict(sort=sort, order=order)
            for sort in ("name", "usage", "expiry", "activity")
            for order in ("asc", "desc")
        ),
        {"status": "active", "tag": "FAMILY", "profile": "nearby"},
        {"q": "a", "profile": "default", "quota": 1},
    ]
    for query in queries:
        record("api/people?" + urlencode(query))
    path = "api/people?sort=usage&limit=2"
    while (page := record(path))["next_cursor"]:
        path = "api/people?" + urlencode({"sort": "usage", "limit": 2, "cursor": page["next_cursor"]})


def record_person_features(record: Recorder, name: str) -> None:
    record(f"api/users/{name}/access")
    record(f"api/users/{name}/delivery-policy")
    for suffix in ("", "info", "json", "mihomo", "stash", "singbox"):
        record(
            f"api/subscription/preview?case={name}-{suffix or 'default'}",
            "POST",
            SubscriptionPreviewRequest(username=name, suffix=suffix).to_data(),
        )


def record_interactive_templates(record: Recorder, service: ControlService) -> dict[str, Any]:
    """Use real replies for all finite membership/policy/format choices; arbitrary visitor edits stay in JS."""
    review = record(
        f"api/users/{PAGE_PERSON}/delivery-policy",
        "PUT",
        PersonDeliveryPolicyWrite(policy="full").to_data(),
    )
    assert not review["confirmed"]
    decisions: dict[str, dict[str, str]] = {}
    for choice, headers in FORMAT_INPUTS.items():
        decisions[choice] = {}
        for suffix in SUFFIXES:
            key = f"api/response-rules/test?case={choice}-{suffix or 'default'}"
            record(key, "POST", ResponseRuleTestRequest(**headers, suffix=suffix).to_data())
            decisions[choice][suffix] = "POST " + key

    template = "DemoTemplate"
    access: dict[str, str] = {}
    previews: dict[str, dict[str, dict[str, str]]] = {}
    profile_names = ("default", *sorted(PROFILES))
    memberships = [
        list(combo) for count in range(1, len(profile_names) + 1) for combo in combinations(profile_names, count)
    ]
    for profiles in memberships:
        service.update_user(template, UserUpdate(profiles=profiles))
        membership = ",".join(sorted(profiles))
        key = "api/users/DemoTemplate/access?profiles=" + membership
        record(key)
        access[membership] = "GET " + key
        previews[membership] = {}
        for policy in ("ru-split", "full", "local-sites"):
            request = PersonDeliveryPolicyWrite(policy=policy)
            preview = service.delivery.set_person(template, request)
            service.delivery.set_person(
                template, PersonDeliveryPolicyWrite(policy=policy, confirm=True, review_hash=preview.review_hash)
            )
            previews[membership][policy] = {}
            for suffix in ("", "info", "json", "mihomo", "stash", "singbox"):
                key = f"api/subscription/preview?case=template-{membership}-{policy}-{suffix or 'default'}"
                result = record(key, "POST", SubscriptionPreviewRequest(username=template, suffix=suffix).to_data())
                mode = result["format"] + (":" + result["response_kind"] if result["format"] == "browser" else "")
                previews[membership][policy][mode] = "POST " + key
                if suffix == "info":
                    page_key = key + "-page"
                    record(
                        page_key, "POST", SubscriptionPreviewRequest(username=template, accept="text/html").to_data()
                    )
                    previews[membership][policy]["browser:page"] = "POST " + page_key
    service.update_user(template, UserUpdate(profiles=["default"]))
    preview = service.delivery.set_person(template, PersonDeliveryPolicyWrite(policy=None))
    service.delivery.set_person(
        template, PersonDeliveryPolicyWrite(policy=None, confirm=True, review_hash=preview.review_hash)
    )
    record(
        "api/subscription/preview?case=blocked",
        "POST",
        SubscriptionPreviewRequest(username=template, user_agent="DemoBlocked/1").to_data(),
    )
    record("api/users/preview", "POST", {"username": template})
    record(
        "api/delivery-policies/explain",
        "POST",
        DeliveryExplainRequest(destination="site.example", person=template).to_data(),
    )
    review = record(
        "api/people/bulk/preview",
        "POST",
        BulkPreviewRequest(usernames=[template], action=BulkAction(action="tag", tag="REVIEWED")).to_data(),
    )
    receipt = record(
        "api/people/bulk/commit", "POST", {"preview_id": review["id"], "preview_hash": review["preview_hash"]}
    )
    record("api/people/bulk/receipts/" + receipt["id"])
    record("api/people/bulk/undo", "POST", {"receipt_id": receipt["id"]})
    record(
        "api/people/bulk/preview?case=declared-delete",
        "POST",
        BulkPreviewRequest(usernames=["Tavi", "Neri"], action=BulkAction(action="delete")).to_data(),
    )
    return {
        "people_fields": list(PeoplePerson.model_fields),
        "bulk_fields": list(BulkFields.model_fields),
        "bulk_writable_fields": list(BULK_WRITABLE_FIELDS),
        "user_writable_fields": list(UserUpdate.model_fields),
        "username_pattern": USERNAME_PATTERN,
        "traffic_limit_max": MAX_TRAFFIC_LIMIT_BYTES,
        "note_limit": MAX_NOTE_CHARACTERS,
        "protocol_order": list(PROTOCOL_PRIORITY),
        "access_templates": access,
        "subscription_templates": previews,
        "response_decisions": decisions,
    }
