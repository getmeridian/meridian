"""Ordered control route bindings, kept apart from the HTTP handler."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from meridian.control import page_routes, subscription_routes
from meridian.control.formats.selector import SUFFIX_FORMATS
from meridian.control.models import SUB_TOKEN_PATTERN, USERNAME_PATTERN, ScopeRequirement, TokenScope
from meridian.core.client_devices import DEVICE_KEY_PATTERN

_NAME = r"([a-z0-9][a-z0-9_.:-]{0,127})"
# A node ID is a topology ID (`core.inputs`); an endpoint ID may also carry `.` and `:`.
_NODE = r"([a-z0-9][a-z0-9_-]{0,47})"
_USERNAME = f"({USERNAME_PATTERN})"
_TOKEN_ID = r"([0-9a-f]{12})"
_REVISION = r"([1-9][0-9]{0,15})"
_SUB_TOKEN = "(" + SUB_TOKEN_PATTERN.removeprefix("^").removesuffix("$") + ")"
_SUB_SUFFIX = "|".join(re.escape(suffix) for suffix in SUFFIX_FORMATS)
_UUID = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
# Topology writes, enrollment, tokens, and backups need admin. People mutations name
# the separate people capability in ROUTES; admin covers both. A tuple allows either
# capability, so user reads remain available without giving people fleet read access.
WRITE: TokenScope = "admin"

Route = tuple[str, re.Pattern[str], ScopeRequirement | None, Callable[[Any, re.Match[str]], Any]]


def control_routes(handler_type: type[Any]) -> list[Route]:
    """Bind the route table to the server's handler without importing the HTTP front."""
    return [
        ("GET", re.compile(r"/api/health"), "read", handler_type.health),
        ("GET", re.compile(r"/api/settings"), "read", handler_type.get_settings),
        ("PUT", re.compile(r"/api/settings"), WRITE, handler_type.put_settings),
        ("GET", re.compile(r"/api/privacy"), "read", handler_type.get_privacy),
        ("PUT", re.compile(r"/api/privacy"), WRITE, handler_type.put_privacy),
        ("GET", re.compile(rf"/api/nodes/{_NODE}"), "read", handler_type.get_node),
        ("PUT", re.compile(rf"/api/nodes/{_NODE}"), WRITE, handler_type.put_node),
        ("DELETE", re.compile(rf"/api/nodes/{_NODE}"), WRITE, handler_type.delete_node),
        ("POST", re.compile(rf"/api/nodes/{_NODE}/enrollment"), "admin", handler_type.grant_enrollment),
        ("POST", re.compile(rf"/api/nodes/{_NODE}/commands"), WRITE, handler_type.issue_command),
        ("GET", re.compile(rf"/api/nodes/{_NODE}/commands/([0-9a-f-]{{36}})"), "read", handler_type.get_command),
        ("GET", re.compile(rf"/api/nodes/{_NODE}/command-targets"), "read", handler_type.command_targets),
        ("GET", re.compile(r"/api/ca"), "read", handler_type.fleet_authority),
        ("GET", re.compile(r"/api/traffic"), "read", handler_type.traffic),
        ("GET", re.compile(r"/api/events"), "read", handler_type.events),
        ("GET", re.compile(r"/api/attention"), "read", handler_type.attention),
        ("GET", re.compile(r"/api/servers"), "read", handler_type.server_usage),
        ("GET", re.compile(r"/api/traffic/daily"), "read", handler_type.daily_traffic),
        ("GET", re.compile(r"/api/users"), ("read", "people"), handler_type.list_users),
        ("POST", re.compile(r"/api/users"), "people", handler_type.create_user),
        # Credentials go in as well as out: importing users (`meridian migrate`) is the root of applying.
        ("POST", re.compile(r"/api/import/users"), "admin", handler_type.import_users),
        ("GET", re.compile(rf"/api/users/{_USERNAME}"), ("read", "people"), handler_type.get_user),
        ("GET", re.compile(rf"/api/users/{_USERNAME}/usage"), ("read", "people"), handler_type.user_usage),
        ("PUT", re.compile(rf"/api/users/{_USERNAME}"), "people", handler_type.put_user),
        ("PATCH", re.compile(rf"/api/users(?:/{_USERNAME})?"), "people", handler_type.update_user),
        ("POST", re.compile(rf"/api/users/{_USERNAME}/reissue"), "people", handler_type.reissue_user),
        ("GET", re.compile(rf"/api/users/{_USERNAME}/cut-off"), "read", handler_type.cut_off_scope),
        ("POST", re.compile(rf"/api/users/{_USERNAME}/reset-traffic"), "people", handler_type.reset_user_traffic),
        ("POST", re.compile(rf"/api/users/{_USERNAME}/extend"), "people", handler_type.extend_user),
        ("GET", re.compile(rf"/api/users/{_USERNAME}/devices"), ("read", "people"), handler_type.user_devices),
        (
            "DELETE",
            re.compile(rf"/api/users/{_USERNAME}/devices/({DEVICE_KEY_PATTERN})"),
            "people",
            handler_type.delete_user_device,
        ),
        ("DELETE", re.compile(rf"/api/users/{_USERNAME}/devices"), "people", handler_type.clear_user_devices),
        ("DELETE", re.compile(rf"/api/users/{_USERNAME}"), "people", handler_type.delete_user),
        ("GET", re.compile(rf"/api/endpoints/{_NAME}"), "read", handler_type.get_endpoint),
        ("PUT", re.compile(rf"/api/endpoints/{_NAME}"), WRITE, handler_type.put_endpoint),
        ("DELETE", re.compile(rf"/api/endpoints/{_NAME}"), WRITE, handler_type.delete_endpoint),
        ("GET", re.compile(r"/api/tokens"), "admin", handler_type.list_tokens),
        ("POST", re.compile(r"/api/tokens"), "admin", handler_type.create_token),
        ("DELETE", re.compile(rf"/api/tokens/{_TOKEN_ID}"), "admin", handler_type.revoke_token),
        ("GET", re.compile(r"/api/revisions"), "read", handler_type.list_revisions),
        ("POST", re.compile(r"/api/revisions"), "propose", handler_type.propose_revision),
        ("GET", re.compile(rf"/api/revisions/{_REVISION}"), "read", handler_type.get_revision),
        ("POST", re.compile(rf"/api/revisions/{_REVISION}/approve"), "approve", handler_type.approve_revision),
        ("POST", re.compile(rf"/api/revisions/{_REVISION}/result"), WRITE, handler_type.record_revision_result),
        ("GET", re.compile(r"/api/fence"), "read", handler_type.get_fence),
        ("PUT", re.compile(r"/api/fence"), WRITE, handler_type.swap_fence),
        # Backups: every route is `admin`, as a snapshot holds every credential the service keeps.
        ("GET", re.compile(r"/api/backup/devices"), "admin", handler_type.list_device_keys),
        ("POST", re.compile(r"/api/backup/devices"), "admin", handler_type.register_device_key),
        ("DELETE", re.compile(rf"/api/backup/devices/{_TOKEN_ID}"), "admin", handler_type.revoke_device_key),
        ("GET", re.compile(r"/api/backup/recovery-key"), "admin", handler_type.get_recovery_key),
        ("PUT", re.compile(r"/api/backup/recovery-key"), "admin", handler_type.put_recovery_key),
        ("PUT", re.compile(r"/api/backup/deployer-state"), "admin", handler_type.deposit_deployer_state),
        ("GET", re.compile(r"/api/backup/snapshot"), "admin", handler_type.snapshot),
        ("POST", re.compile(r"/agent/v1/enroll"), None, handler_type.agent_enroll),
        ("POST", re.compile(r"/agent/v1/hello"), None, handler_type.agent_hello),
        ("POST", re.compile(r"/agent/v1/renew"), None, handler_type.agent_renew),
        ("POST", re.compile(r"/agent/v1/poll"), None, handler_type.agent_poll),
        ("POST", re.compile(r"/agent/v1/report"), None, handler_type.agent_report),
        ("POST", re.compile(r"/agent/v1/stats"), None, handler_type.agent_stats),
        ("POST", re.compile(r"/agent/v1/command-results"), None, handler_type.agent_command_result),
        ("GET", re.compile(r"/agent/v1/blobs/([0-9a-f]{64})"), None, handler_type.agent_blob),
        ("GET", re.compile(r"/sub/~pwa/(?:(pwa-[0-9a-f]+)/)?([a-z][a-z0-9.]+)"), None, page_routes.asset),
        ("GET", re.compile(r"/sub/~legacy/pwa/([a-z][a-z0-9.]+)"), None, page_routes.legacy_asset),
        (
            "GET",
            re.compile(rf"/sub/~legacy/{_UUID}(?:/(index\.html|config\.json|manifest\.webmanifest))?/?"),
            None,
            page_routes.legacy,
        ),
        (
            "GET",
            re.compile(rf"/sub/{_SUB_TOKEN}/(index\.html|config\.json|manifest\.webmanifest)"),
            None,
            page_routes.page,
        ),
        ("GET", re.compile(rf"/sub/{_SUB_TOKEN}(?:/({_SUB_SUFFIX}))?/?"), None, subscription_routes.subscription),
    ]
