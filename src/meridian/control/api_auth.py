"""Bearer authentication, catalog permissions, and credential-safe API projections."""

import hmac
from email.message import Message
from typing import Any, Protocol

from pydantic import BaseModel

from meridian.control.enrollment import token_sha256
from meridian.control.errors import ServiceError
from meridian.control.models import ScopeRequirement, UserRecord
from meridian.control.route_types import ApiRoute
from meridian.control.tokens import ROOT, WEB_OWNER, Caller, unauthenticated
from meridian.control.ui_routes import UiRequest, owner_session
from meridian.core.token_models import Permission


class AuthServer(Protocol):
    admin_token_sha256: str


class AuthRequest(UiRequest, Protocol):
    api_route: ApiRoute | None

    @property
    def server(self) -> AuthServer: ...


def bearer_token(headers: Message) -> str:
    scheme, _, token = headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthenticated()
    return token.strip()


def require_route(caller: Caller, route: ApiRoute, body: dict[str, Any] | None = None) -> None:
    required = set(route.capabilities)
    if body is None:
        alternatives: list[set[Permission]] = [required]
        alternatives.extend({rule.capability} for rule in route.body_scopes if rule.replaces)
        if any(all(caller.allows(capability) for capability in group) for group in alternatives):
            return
    else:
        for rule in route.body_scopes:
            if body.get(rule.field) == rule.equals:
                if rule.replaces:
                    required = {rule.capability}
                else:
                    required.add(rule.capability)
        if all(caller.allows(capability) for capability in required):
            return
    if "root" in required:
        message = "Only the deployer's root token may issue a console recovery link."
    else:
        message = "This token needs the " + ", ".join(sorted(required)) + " permission."
    raise ServiceError("insufficient_scope", message, 403)


def authorize_api(request: AuthRequest, needed: ScopeRequirement) -> Caller:
    if not request.headers.get("Authorization") and request.headers.get("Cookie"):
        owner_session(request)
        caller = WEB_OWNER
    else:
        token = bearer_token(request.headers)
        caller = (
            ROOT
            if hmac.compare_digest(token_sha256(token), request.server.admin_token_sha256)
            else request.ui.service.tokens.authenticate(token)
        )
    if request.api_route is None:
        raise RuntimeError("A protected route is missing capability metadata.")
    if request.api_route.scope != needed:
        raise RuntimeError("The dispatch permission differs from its catalog declaration.")
    require_route(caller, request.api_route)
    return caller


def authorize_body(request: AuthRequest, body: BaseModel) -> None:
    if request.api_route is not None and request.caller is not None:
        require_route(request.caller, request.api_route, body.model_dump(mode="json"))


def visible_user(user: UserRecord, caller: Caller) -> UserRecord:
    changes: dict[str, str] = {}
    if not caller.allows("people.credentials"):
        changes.update(uuid="", sub_token="")
    if not caller.allows("people.private"):
        changes.update(note="", tag="")
    return user.model_copy(update=changes) if changes else user


def visible_result(value: Any, caller: Caller) -> Any:
    """Apply the same credential rules to people nested in any typed API read or mutation result."""
    if isinstance(value, UserRecord):
        return visible_user(value, caller)
    if isinstance(value, BaseModel):
        changes = {}
        for name in type(value).model_fields:
            current = getattr(value, name)
            visible = visible_result(current, caller)
            if visible != current:
                changes[name] = visible
        return value.model_copy(update=changes) if changes else value
    if isinstance(value, list):
        return [visible_result(item, caller) for item in value]
    if isinstance(value, dict):
        return {key: visible_result(item, caller) for key, item in value.items()}
    return value
