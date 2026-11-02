"""Operator delivery API handlers; `route_catalog.py` declares their paths and scopes."""

from __future__ import annotations

import re
from typing import Protocol

from meridian.control.errors import ServiceError
from meridian.control.operations_api import query_options
from meridian.control.tokens import Caller
from meridian.control.ui_routes import UiContext
from meridian.core.languages import LanguageSettings, LanguageState
from meridian.core.notices import NoticeState, NoticeWrite
from meridian.core.subscription_preview import SubscriptionPreview, SubscriptionPreviewRequest


class DeliveryRequest(Protocol):
    path: str
    caller: Caller | None

    @property
    def ui(self) -> UiContext: ...

    def read_body(self) -> bytes: ...


def get_notice(request: DeliveryRequest, _match: re.Match[str]) -> NoticeState:
    options = query_options(request.path, {"for": ""})
    return request.ui.service.notices.show(options["for"] or None)


def put_notice(request: DeliveryRequest, _match: re.Match[str]) -> NoticeState:
    return request.ui.service.notices.update(NoticeWrite.model_validate_json(request.read_body()))


def get_language(request: DeliveryRequest, _match: re.Match[str]) -> LanguageState:
    return request.ui.service.languages.read()


def put_language(request: DeliveryRequest, _match: re.Match[str]) -> LanguageState:
    return request.ui.service.languages.set(LanguageSettings.model_validate_json(request.read_body()))


def subscription_preview(request: DeliveryRequest, _match: re.Match[str]) -> SubscriptionPreview:
    body = SubscriptionPreviewRequest.model_validate_json(request.read_body())
    private = request.caller is not None and request.caller.allows("people.private")
    if request.ui.service.settings().access_profiles and (
        request.caller is None or not request.caller.allows("people.private")
    ):
        raise ServiceError("insufficient_scope", "Access profile previews need the people.private permission.", 403)
    if body.destination is not None and not private:
        raise ServiceError("insufficient_scope", "Destination policy previews need people.private.", 403)
    return request.ui.service.preview_subscription(body, include_delivery=private)


DELIVERY_OWNER_ROUTES = frozenset({get_notice, put_notice, get_language, put_language, subscription_preview})
