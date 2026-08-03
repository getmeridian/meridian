"""Typed control-plane resources implemented over stable REST payloads."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .models import (
    ConfigProfile,
    ExternalSquad,
    Host,
    InternalSquad,
    Node,
    SubscriptionDocument,
    SubscriptionSettings,
    SubscriptionTemplate,
    User,
    parse_config_profile,
    parse_external_squad,
    parse_host,
    parse_internal_squad,
    parse_node,
    parse_subscription_settings,
    parse_subscription_template,
    parse_user,
    response_items,
)
from .runtime import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)

XRAY_JSON_CLIENT_TYPE = "json"


class ControlPlaneMixin:
    """Resource methods shared by the synchronous panel facade."""

    _base: str
    _client: httpx.Client

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError

    # --- Config profiles and node bindings ---

    def update_config_profile(
        self,
        uuid: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> ConfigProfile:
        body: dict[str, Any] = {"uuid": uuid}
        if name is not None:
            body["name"] = name
        if config is not None:
            body["config"] = config
        return parse_config_profile(self._request("PATCH", "/api/config-profiles", json=body))

    def delete_config_profile(self, uuid: str) -> bool:
        data = self._request("DELETE", f"/api/config-profiles/{quote(uuid, safe='')}")
        return bool(data.get("isDeleted")) if isinstance(data, dict) else False

    def bind_node_profile(
        self,
        node_uuid: str,
        profile_uuid: str,
        inbound_uuids: list[str],
    ) -> Node:
        data = self._request(
            "PATCH",
            "/api/nodes",
            json={
                "uuid": node_uuid,
                "configProfile": {
                    "activeConfigProfileUuid": profile_uuid,
                    "activeInbounds": inbound_uuids,
                },
            },
        )
        return parse_node(data)

    # --- Hosts ---

    def get_host(self, uuid: str) -> Host | None:
        try:
            data = self._request("GET", f"/api/hosts/{quote(uuid, safe='')}")
        except RemnawaveNotFoundError:
            return None
        return parse_host(data)

    # --- Internal squads ---

    def list_internal_squads(self) -> list[InternalSquad]:
        data = self._request("GET", "/api/internal-squads")
        return [parse_internal_squad(item) for item in response_items(data, "internalSquads", "internal_squads")]

    def get_internal_squad(self, uuid: str) -> InternalSquad | None:
        try:
            data = self._request("GET", f"/api/internal-squads/{quote(uuid, safe='')}")
        except RemnawaveNotFoundError:
            return None
        return parse_internal_squad(data)

    def create_internal_squad(self, name: str, inbound_uuids: list[str]) -> InternalSquad:
        data = self._request(
            "POST",
            "/api/internal-squads",
            json={"name": name, "inbounds": inbound_uuids},
        )
        return parse_internal_squad(data)

    def update_internal_squad(
        self,
        uuid: str,
        *,
        name: str | None = None,
        inbound_uuids: list[str] | None = None,
    ) -> InternalSquad:
        body: dict[str, Any] = {"uuid": uuid}
        if name is not None:
            body["name"] = name
        if inbound_uuids is not None:
            body["inbounds"] = inbound_uuids
        return parse_internal_squad(self._request("PATCH", "/api/internal-squads", json=body))

    def assign_inbounds_to_squad(self, squad_uuid: str, inbound_uuids: list[str]) -> None:
        self.update_internal_squad(squad_uuid, inbound_uuids=inbound_uuids)

    def delete_internal_squad(self, uuid: str) -> bool:
        data = self._request("DELETE", f"/api/internal-squads/{quote(uuid, safe='')}")
        return bool(data.get("isDeleted")) if isinstance(data, dict) else False

    # --- External squads ---

    def list_external_squads(self) -> list[ExternalSquad]:
        data = self._request("GET", "/api/external-squads")
        return [parse_external_squad(item) for item in response_items(data, "externalSquads", "external_squads")]

    def get_external_squad(self, uuid: str) -> ExternalSquad | None:
        try:
            data = self._request("GET", f"/api/external-squads/{quote(uuid, safe='')}")
        except RemnawaveNotFoundError:
            return None
        return parse_external_squad(data)

    def create_external_squad(self, name: str) -> ExternalSquad:
        return parse_external_squad(self._request("POST", "/api/external-squads", json={"name": name}))

    def update_external_squad(
        self,
        uuid: str,
        *,
        name: str | None = None,
        templates: list[dict[str, str]] | None = None,
        subscription_settings: dict[str, Any] | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> ExternalSquad:
        body: dict[str, Any] = {"uuid": uuid}
        if name is not None:
            body["name"] = name
        if templates is not None:
            body["templates"] = [
                {
                    "templateUuid": item["template_uuid"],
                    "templateType": item["template_type"],
                }
                for item in templates
            ]
        if subscription_settings is not None:
            body["subscriptionSettings"] = subscription_settings
        if response_headers is not None:
            body["responseHeaders"] = response_headers
        return parse_external_squad(self._request("PATCH", "/api/external-squads", json=body))

    def delete_external_squad(self, uuid: str) -> bool:
        data = self._request("DELETE", f"/api/external-squads/{quote(uuid, safe='')}")
        return bool(data.get("isDeleted")) if isinstance(data, dict) else False

    # --- Managed access and service users ---

    def create_access_user(
        self,
        username: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
        expire_at: str = "2099-12-31T23:59:59.000Z",
    ) -> User:
        body: dict[str, Any] = {
            "username": username,
            "expireAt": expire_at,
            "description": "Managed by Meridian access",
            "tag": "MERIDIAN_ACCESS",
            "activeInternalSquads": squad_uuids,
        }
        if external_squad_uuid:
            body["externalSquadUuid"] = external_squad_uuid
        return parse_user(self._request("POST", "/api/users", json=body))

    def update_access_user(
        self,
        uuid: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
    ) -> User:
        return parse_user(
            self._request(
                "PATCH",
                "/api/users",
                json={
                    "uuid": uuid,
                    "activeInternalSquads": squad_uuids,
                    "externalSquadUuid": external_squad_uuid or None,
                },
            )
        )

    def create_service_user(
        self,
        username: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
        expire_at: str = "2099-12-31T23:59:59.000Z",
    ) -> User:
        body: dict[str, Any] = {
            "username": username,
            "expireAt": expire_at,
            "description": "Managed by Meridian service routing",
            "tag": "MERIDIAN_SERVICE",
            "activeInternalSquads": squad_uuids,
        }
        if external_squad_uuid:
            body["externalSquadUuid"] = external_squad_uuid
        return parse_user(self._request("POST", "/api/users", json=body))

    def update_service_user(
        self,
        uuid: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
    ) -> User:
        body: dict[str, Any] = {
            "uuid": uuid,
            "activeInternalSquads": squad_uuids,
            "externalSquadUuid": external_squad_uuid or None,
        }
        return parse_user(self._request("PATCH", "/api/users", json=body))

    def delete_service_user(self, uuid: str) -> bool:
        data = self._request("DELETE", f"/api/users/{quote(uuid, safe='')}")
        return bool(data.get("isDeleted")) if isinstance(data, dict) else False

    # --- Subscription templates ---

    def list_subscription_templates(self) -> list[SubscriptionTemplate]:
        data = self._request("GET", "/api/subscription-templates")
        return [parse_subscription_template(item) for item in response_items(data, "templates")]

    def get_subscription_template(self, uuid: str) -> SubscriptionTemplate | None:
        try:
            data = self._request(
                "GET",
                f"/api/subscription-templates/{quote(uuid, safe='')}",
            )
        except RemnawaveNotFoundError:
            return None
        return parse_subscription_template(data)

    def create_subscription_template(
        self,
        name: str,
        template_type: str,
    ) -> SubscriptionTemplate:
        data = self._request(
            "POST",
            "/api/subscription-templates",
            json={"name": name, "templateType": template_type},
        )
        return parse_subscription_template(data)

    def update_subscription_template(
        self,
        uuid: str,
        *,
        name: str | None = None,
        template_json: dict[str, Any] | None = None,
        encoded_template_yaml: str | None = None,
    ) -> SubscriptionTemplate:
        body: dict[str, Any] = {"uuid": uuid}
        if name is not None:
            body["name"] = name
        if template_json is not None:
            body["templateJson"] = template_json
        if encoded_template_yaml is not None:
            body["encodedTemplateYaml"] = encoded_template_yaml
        return parse_subscription_template(self._request("PATCH", "/api/subscription-templates", json=body))

    def delete_subscription_template(self, uuid: str) -> bool:
        data = self._request(
            "DELETE",
            f"/api/subscription-templates/{quote(uuid, safe='')}",
        )
        return bool(data.get("isDeleted")) if isinstance(data, dict) else False

    # --- Subscription settings and canonical content ---

    def get_subscription_settings(self) -> SubscriptionSettings:
        return parse_subscription_settings(self._request("GET", "/api/subscription-settings"))

    def update_subscription_settings(
        self,
        uuid: str,
        *,
        profile_title: str | None = None,
        support_link: str | None = None,
        profile_update_interval: int | None = None,
        serve_json_at_base_subscription: bool | None = None,
        randomize_hosts: bool | None = None,
        response_rules: dict[str, Any] | None = None,
        custom_response_headers: dict[str, str] | None = None,
    ) -> SubscriptionSettings:
        body: dict[str, Any] = {"uuid": uuid}
        optional_fields: tuple[tuple[str, Any], ...] = (
            ("profileTitle", profile_title),
            ("supportLink", support_link),
            ("profileUpdateInterval", profile_update_interval),
            ("serveJsonAtBaseSubscription", serve_json_at_base_subscription),
            ("randomizeHosts", randomize_hosts),
            ("responseRules", response_rules),
            ("customResponseHeaders", custom_response_headers),
        )
        for key, value in optional_fields:
            if value is not None:
                body[key] = value
        return parse_subscription_settings(self._request("PATCH", "/api/subscription-settings", json=body))

    def fetch_subscription(
        self,
        short_uuid: str,
        *,
        client_type: str = "",
    ) -> SubscriptionDocument:
        encoded_uuid = quote(short_uuid, safe="")
        encoded_type = quote(client_type, safe="") if client_type else ""
        path = f"/api/sub/{encoded_uuid}"
        if encoded_type:
            path = f"{path}/{encoded_type}"
        try:
            response = self._client.request("GET", path.lstrip("/"))
        except httpx.RequestError as exc:
            raise RemnawaveNetworkError(
                f"Could not fetch canonical subscription: {exc}",
                category="system",
            ) from exc
        if response.status_code in (401, 403):
            raise RemnawaveAuthError(
                f"Subscription access failed ({response.status_code})",
                category="user",
            )
        if response.status_code == 404:
            raise RemnawaveNotFoundError("Subscription not found", category="user")
        if response.status_code >= 400:
            raise RemnawaveError(
                f"Subscription fetch failed ({response.status_code}): {response.text[:200]}",
                category="system" if response.status_code == 429 or response.status_code >= 500 else "user",
            )
        url = f"{self._base}{path}"
        return SubscriptionDocument(
            url=url,
            content=response.text,
            client_type=client_type,
            content_type=response.headers.get("content-type", ""),
        )
