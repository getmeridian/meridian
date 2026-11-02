"""Read one service view for response tests without registering devices or retaining headers."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from meridian.control.errors import ServiceError
from meridian.control.formats.response_rules import evaluate_response_rules
from meridian.control.models import ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.core.redaction import REDACTED, redact_preview_text
from meridian.core.response_rule_test import ResponseRuleDecision, ResponseRuleTestRequest
from meridian.core.response_rules import ResponseHeaderName


def safe_response_headers(headers: dict[str, str], known: Sequence[str] = ()) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in headers.items():
        name = name.lower()
        if name in ("support-url", "profile-web-page-url") and value:
            result[name] = REDACTED
        elif name in ("profile-title", "announce") and value:
            result[name] = "base64:" + REDACTED
        else:
            result[name] = redact_preview_text(value, list(known))
    return result


def safe_response_decision(decision: ResponseRuleDecision, *, include_policy: bool = True) -> ResponseRuleDecision:
    headers: dict[ResponseHeaderName, str] = {
        name: safe_response_headers({name: value})[name] for name, value in decision.headers.items()
    }
    return decision.model_copy(update={"headers": headers, "policy": decision.policy if include_policy else None})


def test_response_rules(
    store: Store,
    request: ResponseRuleTestRequest,
    *,
    read_settings: Callable[[], ServiceSettings],
    read_user: Callable[[str], UserRecord | None],
) -> ResponseRuleDecision:
    with store.lock:
        person = read_user(request.person) if request.person is not None else None
        if request.person is not None and person is None:
            raise ServiceError("not_found", "Unknown person. Check names with meridian client list.", 404)
        decision = evaluate_response_rules(
            read_settings(),
            user_agent=request.user_agent,
            accept=request.accept,
            suffix=request.suffix,
            profiles=person.profiles if person is not None else (),
            person_override=person.delivery_policy if person is not None else None,
        )
    return safe_response_decision(decision)
