"""Protected Prometheus text on the existing control ingress."""

import re
from typing import Protocol

from meridian.control.metrics_export import METRICS_CONTENT_TYPE, exposition
from meridian.control.request_metrics import RequestMetrics
from meridian.control.ui_routes import UiRequest


class MetricsServer(Protocol):
    requests: RequestMetrics


class MetricsRequest(UiRequest, Protocol):
    server: MetricsServer


def metrics(request: MetricsRequest, _match: re.Match[str]) -> None:
    text = exposition(request.ui.service, request.server.requests)
    request.respond(200, METRICS_CONTENT_TYPE, text.root.encode("utf-8"))
