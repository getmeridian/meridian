"""Operator delivery APIs, registered separately from the HTTP front's route table."""

from __future__ import annotations

import re
from typing import Any, Protocol

from meridian.control.models import ScopeRequirement
from meridian.control.operations_api import query_options
from meridian.control.ui_routes import UiContext
from meridian.core.notices import NoticeState, NoticeWrite


class DeliveryRequest(Protocol):
    path: str

    @property
    def ui(self) -> UiContext: ...

    def read_body(self) -> bytes: ...


def get_notice(request: DeliveryRequest, _match: re.Match[str]) -> NoticeState:
    options = query_options(request.path, {"for": ""})
    return request.ui.service.notices.show(options["for"] or None)


def put_notice(request: DeliveryRequest, _match: re.Match[str]) -> NoticeState:
    return request.ui.service.notices.update(NoticeWrite.model_validate_json(request.read_body()))


DELIVERY_ROUTES: list[tuple[str, re.Pattern[str], ScopeRequirement | None, Any]] = [
    ("GET", re.compile(r"/api/notice"), "read", get_notice),
    ("PUT", re.compile(r"/api/notice"), "admin", put_notice),
]
DELIVERY_OWNER_ROUTES = frozenset({get_notice, put_notice})
