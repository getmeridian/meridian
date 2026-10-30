"""Subscription refill dates follow the UTC periods enforcement uses."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import DocumentFormat
from meridian.control.models import LimitStrategy, UserUpdate
from meridian.control.service import ControlService
from meridian.control.subscriptions import render_subscription
from tests.support.control_service import FLEET_ID
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, UUID


@pytest.mark.parametrize("document", ["base64", "xray-json", "mihomo", "stash", "singbox", "browser"])
@pytest.mark.parametrize(
    ("strategy", "now", "next_start"),
    [
        ("no_reset", "2026-10-29T12:00:00Z", None),
        ("day", "2028-02-29T23:59:59Z", "2028-03-01T00:00:00Z"),
        ("day", "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"),
        ("week", "2026-11-01T23:59:59Z", "2026-11-02T00:00:00Z"),
        ("week", "2026-11-02T00:00:00Z", "2026-11-09T00:00:00Z"),
        ("month", "2028-02-29T12:00:00Z", "2028-03-01T00:00:00Z"),
        ("month", "2026-12-31T23:59:59Z", "2027-01-01T00:00:00Z"),
        ("month", "2026-10-31T23:30:00-02:00", "2026-12-01T00:00:00Z"),
    ],
    ids=["no-reset", "leap-day", "midnight", "sunday", "monday", "leap-month", "year-end", "utc-month"],
)
def test_refill_dates_are_the_next_utc_reset_boundary(
    document: DocumentFormat, strategy: LimitStrategy, now: str, next_start: str | None
) -> None:
    person = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID, limit_strategy=strategy)
    response = render_subscription(
        person, FLEETS["single"].endpoints, document, profile_title="Meridian", now=datetime.fromisoformat(now)
    )
    expected = str(int(datetime.fromisoformat(next_start).timestamp())) if next_start else None
    assert response.headers.get("subscription-refill-date") == expected


def test_subscription_refill_uses_the_service_clock(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2028-02-29T23:59:59Z")
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=now.timestamp)
    try:
        user = service.put_user("person")
        service.update_user(user.username, UserUpdate(traffic_limit_strategy="month"))
        response = service.subscription(user.sub_token)
        assert response.headers.get("subscription-refill-date") == str(
            int(datetime.fromisoformat("2028-03-01T00:00:00Z").timestamp())
        )
    finally:
        service.close()
