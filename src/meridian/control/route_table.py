"""Bind cataloged API routes and explicitly internal HTTP surfaces."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from meridian.control import page_routes, subscription_routes
from meridian.control.formats.selector import SUFFIX_FORMATS
from meridian.control.models import SUB_TOKEN_PATTERN, USERNAME_PATTERN, ScopeRequirement
from meridian.control.route_catalog import API_ROUTES
from meridian.control.route_types import Handler, path_pattern

Route = tuple[str, re.Pattern[str], ScopeRequirement | None, Callable[[Any, re.Match[str]], Any]]
_SUB_TOKEN = "(" + SUB_TOKEN_PATTERN.removeprefix("^").removesuffix("$") + ")"
_SUB_SUFFIX = "|".join(re.escape(suffix) for suffix in SUFFIX_FORMATS)
_UUID = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"


@dataclass(frozen=True)
class InternalRoute:
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    reason: str


INTERNAL_ROUTES: tuple[InternalRoute, ...] = (
    InternalRoute("POST", re.compile(r"/agent/v1/enroll"), "agent_enroll", "Internal agent enrollment protocol."),
    InternalRoute("POST", re.compile(r"/agent/v1/hello"), "agent_hello", "Internal agent certificate authentication."),
    InternalRoute("POST", re.compile(r"/agent/v1/renew"), "agent_renew", "Internal agent session renewal."),
    InternalRoute("POST", re.compile(r"/agent/v1/poll"), "agent_poll", "Internal agent desired-state polling."),
    InternalRoute("POST", re.compile(r"/agent/v1/report"), "agent_report", "Internal agent runtime reports."),
    InternalRoute("POST", re.compile(r"/agent/v1/stats"), "agent_stats", "Internal aggregate traffic reporting."),
    InternalRoute(
        "POST",
        re.compile(r"/agent/v1/command-results"),
        "agent_command_result",
        "Internal signed command acknowledgements.",
    ),
    InternalRoute(
        "GET", re.compile(r"/agent/v1/blobs/([0-9a-f]{64})"), "agent_blob", "Internal authenticated artifact mirror."
    ),
    InternalRoute(
        "GET",
        re.compile(r"/sub/~pwa/(?:(pwa-[0-9a-f]+)/)?([a-z][a-z0-9.]+)"),
        page_routes.asset,
        "Public connection-page package assets.",
    ),
    InternalRoute(
        "GET",
        re.compile(r"/sub/~legacy/pwa/([a-z][a-z0-9.]+)"),
        page_routes.legacy_asset,
        "Public legacy connection-page assets.",
    ),
    InternalRoute(
        "GET",
        re.compile(rf"/sub/~legacy/{_UUID}(?:/(index\.html|config\.json|manifest\.webmanifest))?/?"),
        page_routes.legacy,
        "Public legacy connection-page redirects.",
    ),
    InternalRoute(
        "GET",
        re.compile(rf"/sub/{_SUB_TOKEN}/(index\.html|config\.json|manifest\.webmanifest)"),
        page_routes.page,
        "Public personal connection-page content.",
    ),
    InternalRoute(
        "GET",
        re.compile(rf"/sub/{_SUB_TOKEN}(?:/({_SUB_SUFFIX}))?/?"),
        subscription_routes.subscription,
        "Public token-authenticated subscription delivery.",
    ),
)

# The console keeps its handler table in ui_routes.py. These explicit exceptions
# make a new console route require a publication decision too.
INTERNAL_UI_ROUTES: dict[tuple[str, str], str] = {
    ("GET", r"/"): "Console shell, outside the bearer-token API.",
    ("GET", r"/ui/((?:locales/)?[a-z0-9-]+\.(?:js|css|svg|json))"): "Local console assets.",
    ("GET", r"/auth/state"): "Console session state.",
    ("POST", r"/auth/setup/check"): "Console owner setup-link validation.",
    ("POST", r"/auth/setup"): "Console owner setup.",
    ("POST", r"/auth/login"): "Console password/TOTP sign-in.",
    ("POST", r"/auth/logout"): "Console session sign-out.",
    ("POST", r"/auth/confirm"): "Console password/TOTP confirmation.",
    ("GET", r"/auth/sessions"): "Console owner session listing.",
    ("POST", r"/auth/sessions/revoke-others"): "Console session revocation.",
    ("POST", r"/auth/password"): "Console owner password change.",
    ("POST", r"/auth/totp/begin"): "Console authenticator setup.",
    ("POST", r"/auth/totp/enable"): "Console authenticator enablement.",
    ("POST", r"/auth/totp/disable"): "Console authenticator removal.",
    ("GET", r"/ui/api/fleet"): "Console-specific fleet projection.",
    ("GET", rf"/ui/api/people/({USERNAME_PATTERN})/share"): "Console-specific connection sharing.",
    ("GET", rf"/ui/api/people/({USERNAME_PATTERN})/expiry"): "Console-specific expiry renewal preview.",
}


def control_routes(handler_type: type[Any]) -> list[Route]:
    """Dispatch and scope checks consume the same catalog as OpenAPI generation."""

    def bind(handler: Handler) -> Callable[[Any, re.Match[str]], Any]:
        return getattr(handler_type, handler) if isinstance(handler, str) else handler

    return [(item.method, path_pattern(item.path), item.scope, bind(item.handler)) for item in API_ROUTES] + [
        (item.method, item.pattern, None, bind(item.handler)) for item in INTERNAL_ROUTES
    ]
