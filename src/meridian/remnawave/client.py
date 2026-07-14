"""Typed synchronous facade over the official Remnawave SDK."""

from __future__ import annotations

import logging
import random
import time
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from remnawave import RemnawaveSDK
from remnawave.models.config_profiles import CreateConfigProfileRequestDto
from remnawave.models.nodes import CreateNodeRequestDto, NodeConfigProfileRequestDto
from remnawave.models.users import CreateUserRequestDto

from .control_plane import ControlPlaneMixin
from .models import (
    ConfigProfile,
    Host,
    Inbound,
    Node,
    NodeCredentials,
    User,
    config_profile_from_sdk,
    host_from_sdk,
    inbound_from_sdk,
    node_from_sdk,
    parse_host,
    sdk_items,
    sdk_to_dict,
    user_from_sdk,
)
from .runtime import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
    sdk_call,
)

logger = logging.getLogger("meridian.api")


class MeridianPanel(ControlPlaneMixin):
    """Remnawave API client — sync facade over the official SDK.

    Uses the official ``remnawave`` SDK for panel operations and falls
    back to raw httpx only for auth helpers and generic low-level access.

    Domain groups (natural extraction boundaries):
      - Auth (login, register_admin) — classmethods, no SDK instance
      - Users — CRUD for Meridian clients (= Remnawave users)
      - Nodes — register, list, enable/disable, delete proxy nodes
      - Hosts — direct addresses and relay entries in subscriptions
      - Config Profiles — Xray configuration templates
      - Inbounds — protocol definitions within config profiles
      - Internal Squads — user-to-inbound access control groups
      - Keygen — node mTLS secret bundles
      - Subscriptions — subscription URL building
      - Xray Config — global DNS/routing configuration

    Usage:
        panel = MeridianPanel("https://panel.example.com", "jwt-token")
        user = panel.create_user("alice")
        nodes = panel.list_nodes()
    """

    def __init__(self, base_url: str, api_token: str, *, timeout: int = 30, max_retries: int = 5):
        import httpx

        self._base = base_url.rstrip("/")
        self._token = api_token
        self._timeout = timeout
        self._max_retries = max_retries

        # SDK client for supported endpoints — ssl_ignore for self-signed certs
        # (panel is reverse-proxied by nginx with a self-signed cert in IP mode)
        self._sdk = RemnawaveSDK(base_url=self._base, token=api_token, ssl_ignore=True)

        # Raw httpx client for endpoints the SDK doesn't cover
        self._client = httpx.Client(
            base_url=self._base + "/",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=False,
        )

    def close(self) -> None:
        """Close the underlying HTTP clients."""
        self._client.close()

    def __enter__(self) -> MeridianPanel:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # --- Low-level raw request (for endpoints not in SDK) ---

    def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        """Make a raw API request with retry logic (for SDK gaps)."""
        import httpx

        path = path.lstrip("/")
        logger.debug("%s %s", method, path)
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.request(method, path, json=json, params=params)
                if resp.status_code == 401:
                    raise RemnawaveAuthError(
                        "Panel authentication failed (401)",
                        hint="Check your API token — it may have expired",
                        category="user",
                    )
                if resp.status_code == 403:
                    raise RemnawaveAuthError(
                        "Panel access forbidden (403)",
                        hint="API token lacks required permissions",
                        category="user",
                    )
                if resp.status_code == 404:
                    raise RemnawaveNotFoundError(f"Resource not found: {path}", category="system")
                if resp.status_code >= 500:
                    last_error = RemnawaveError(f"Panel server error ({resp.status_code}): {resp.text[:200]}")
                    if attempt < self._max_retries - 1:
                        time.sleep(min(2**attempt, 16) + random.uniform(0, 1))
                        continue
                    raise last_error
                resp.raise_for_status()
                try:
                    data = resp.json()
                except (ValueError, UnicodeDecodeError) as e:
                    raise RemnawaveError(
                        f"Panel returned invalid JSON ({len(resp.content)} bytes)",
                        hint=f"Response may be truncated by firewall or DPI: {e}",
                        category="system",
                    ) from e
                if isinstance(data, dict) and "response" in data:
                    return data["response"]
                return data
            except httpx.ConnectError as e:
                last_error = RemnawaveNetworkError(
                    f"Cannot connect to panel at {self._base}",
                    hint=f"Is the panel running? Check: curl {self._base}/api/health\n{e}",
                    category="system",
                )
                if attempt < self._max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            except httpx.TimeoutException as e:
                last_error = RemnawaveNetworkError(
                    f"Panel request timed out after {self._timeout}s",
                    hint=f"Panel may be overloaded or unreachable: {e}",
                    category="system",
                )
                if attempt < self._max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            except httpx.HTTPStatusError as e:
                raise RemnawaveError(f"Panel API error: {e.response.status_code} {e.response.text[:200]}") from e

        if last_error:
            raise last_error
        raise RemnawaveError("Request failed after retries")

    def _post(self, path: str, json: Any = None) -> Any:
        return self._request("POST", path, json=json)

    def _patch(self, path: str, json: Any = None) -> Any:
        return self._request("PATCH", path, json=json)

    # --- Health ---

    def ping(self) -> bool:
        """Check if the panel is reachable and authenticated."""
        try:
            sdk_call(self._sdk.users.get_all_users(start=0, size=1))
            return True
        except RemnawaveAuthError:
            raise
        except (RemnawaveError, httpx.HTTPError, OSError):
            return False

    # --- Users (= Meridian clients) ---

    def create_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int = 0,
        expire_at: str = "",
        squad_uuids: list[str] | None = None,
    ) -> User:
        """Create a new user in Remnawave.

        SDK v2 supports ``active_internal_squads`` directly.
        """
        body = CreateUserRequestDto(
            username=username,
            expire_at=expire_at or "2099-12-31T23:59:59.000Z",
            traffic_limit_bytes=traffic_limit_bytes if traffic_limit_bytes > 0 else None,
            active_internal_squads=squad_uuids or None,
        )
        resp = sdk_call(self._sdk.users.create_user(body))
        return user_from_sdk(resp)

    def get_user(self, username: str) -> User | None:
        """Find a user by username. Returns None only if not found (404)."""
        try:
            resp = sdk_call(self._sdk.users.get_user_by_username(username))
            return user_from_sdk(resp)
        except RemnawaveNotFoundError:
            return None

    def get_user_by_uuid(self, uuid: str) -> User | None:
        """Find a user by UUID. Returns None only if not found (404)."""
        try:
            resp = sdk_call(self._sdk.users.get_user_by_uuid(uuid))
            return user_from_sdk(resp)
        except RemnawaveNotFoundError:
            return None

    def list_users(self) -> list[User]:
        """List all users."""
        # Panel limits max size per page; paginate if needed
        all_users: list[User] = []
        start = 0
        page_size = 1000
        while True:
            resp = sdk_call(self._sdk.users.get_all_users(start=start, size=page_size))
            users_list = getattr(resp, "users", []) or []
            if not users_list:
                break
            all_users.extend(user_from_sdk(u) for u in users_list)
            if len(users_list) < page_size:
                break
            start += page_size
        return all_users

    def delete_user(self, uuid: str) -> bool:
        """Delete a user by UUID. Returns False only if not found."""
        try:
            sdk_call(self._sdk.users.delete_user(uuid))
            return True
        except RemnawaveNotFoundError:
            return False

    def enable_user(self, uuid: str) -> None:
        """Enable a disabled user."""
        sdk_call(self._sdk.users.enable_user(uuid))

    def disable_user(self, uuid: str) -> None:
        """Disable a user."""
        sdk_call(self._sdk.users.disable_user(uuid))

    # --- Nodes ---

    def create_node(
        self,
        name: str,
        address: str,
        port: int,
        *,
        config_profile_uuid: str,
        inbound_uuids: list[str] | None = None,
        country_code: str = "XX",
    ) -> NodeCredentials:
        """Register a new node with the panel.

        Returns credentials including the SECRET_KEY for the node container.
        """
        body = CreateNodeRequestDto(
            name=name,
            address=address,
            port=port,
            country_code=country_code,
            config_profile=NodeConfigProfileRequestDto(
                activeConfigProfileUuid=config_profile_uuid,
                activeInbounds=inbound_uuids or [],
            ),
        )
        resp = sdk_call(self._sdk.nodes.create_node(body))
        data = sdk_to_dict(resp)
        node_uuid = str(getattr(resp, "uuid", ""))

        return NodeCredentials(uuid=node_uuid, secret_key=self.get_node_secret_key(), _raw=data)

    def list_nodes(self) -> list[Node]:
        """List all registered nodes."""
        resp = sdk_call(self._sdk.nodes.get_all_nodes())
        nodes_list = sdk_items(resp)
        return [node_from_sdk(n) for n in nodes_list]

    def find_node_by_address(self, address: str) -> Node | None:
        """Find a node by its address. Returns None if not found."""
        for node in self.list_nodes():
            if node.address == address:
                return node
        return None

    def get_node(self, uuid: str) -> Node | None:
        """Get a node by UUID. Returns None only if not found (404)."""
        try:
            resp = sdk_call(self._sdk.nodes.get_one_node(uuid))
            return node_from_sdk(resp)
        except RemnawaveNotFoundError:
            return None

    def enable_node(self, uuid: str) -> None:
        """Enable a disabled node."""
        sdk_call(self._sdk.nodes.enable_node(uuid))

    def disable_node(self, uuid: str) -> None:
        """Disable a node."""
        sdk_call(self._sdk.nodes.disable_node(uuid))

    def restart_node(self, uuid: str) -> None:
        """Restart Xray on a node."""
        sdk_call(self._sdk.nodes.restart_node(uuid))

    def delete_node(self, uuid: str) -> None:
        """Deregister a node."""
        sdk_call(self._sdk.nodes.delete_node(uuid))

    def update_node_name(self, uuid: str, name: str) -> None:
        """Update a node's name in the panel."""
        from remnawave.models.nodes import UpdateNodeRequestDto

        body = UpdateNodeRequestDto(uuid=UUID(uuid), name=name)
        sdk_call(self._sdk.nodes.update_node(body))

    # --- Hosts (relays map here) ---

    def create_host(
        self,
        *,
        remark: str,
        address: str,
        port: int,
        config_profile_uuid: str,
        inbound_uuid: str,
        sni: str = "",
        host_header: str = "",
        path: str = "",
        alpn: str | None = None,
        fingerprint: str | None = None,
        security_layer: str = "DEFAULT",
        is_disabled: bool = False,
    ) -> Host:
        """Create a host entry (direct address or relay).

        Uses raw httpx: SDK's CreateHostRequestDto enforces a strict enum
        for security_layer (DEFAULT/TLS/NONE), but Meridian uses "REALITY"
        for relay hosts which is accepted by the panel API directly.
        """
        body: dict[str, Any] = {
            "remark": remark,
            "address": address,
            "port": port,
            "inbound": {
                "configProfileUuid": config_profile_uuid,
                "configProfileInboundUuid": inbound_uuid,
            },
        }
        if sni:
            body["sni"] = sni
        if host_header:
            body["host"] = host_header
        if path:
            body["path"] = path
        if alpn is not None:
            body["alpn"] = alpn
        if fingerprint is not None:
            body["fingerprint"] = fingerprint
        if security_layer != "DEFAULT":
            body["securityLayer"] = security_layer
        if is_disabled:
            body["isDisabled"] = True
        data = self._post("/api/hosts", json=body)
        return parse_host(data)

    def list_hosts(self) -> list[Host]:
        """List all host entries."""
        resp = sdk_call(self._sdk.hosts.get_all_hosts())
        hosts_list = sdk_items(resp)
        return [host_from_sdk(h) for h in hosts_list]

    def update_host(
        self,
        uuid: str,
        *,
        remark: str,
        address: str,
        port: int,
        config_profile_uuid: str,
        inbound_uuid: str,
        sni: str = "",
        host_header: str = "",
        path: str = "",
        alpn: str | None = None,
        fingerprint: str | None = None,
        security_layer: str = "DEFAULT",
        is_disabled: bool = False,
    ) -> Host:
        """Replace all Meridian-owned Host connection fields."""
        body: dict[str, Any] = {
            "uuid": uuid,
            "remark": remark,
            "address": address,
            "port": port,
            "inbound": {
                "configProfileUuid": config_profile_uuid,
                "configProfileInboundUuid": inbound_uuid,
            },
            "sni": sni or None,
            "host": host_header or None,
            "path": path or None,
            "alpn": alpn,
            "fingerprint": fingerprint,
            "securityLayer": security_layer,
            "isDisabled": is_disabled,
        }
        data = self._patch("/api/hosts", json=body)
        return parse_host(data)

    def find_host_by_remark(self, remark: str) -> Host | None:
        """Find a host by its remark string. Returns None if not found."""
        for host in self.list_hosts():
            if host.remark == remark:
                return host
        return None

    def reorder_hosts(self, ordered_uuids: list[str]) -> None:
        """Set explicit viewPosition for hosts to enforce subscription ordering.

        Hosts appear in subscriptions ordered by viewPosition ASC. This
        ensures safest-first ordering (Reality → XHTTP → WSS) regardless
        of creation order or manual panel UI reordering.
        """
        from remnawave.models.hosts import ReorderHostItem, ReorderHostRequestDto

        items = [ReorderHostItem(uuid=host_uuid, view_position=i) for i, host_uuid in enumerate(ordered_uuids)]
        sdk_call(self._sdk.hosts.reorder_hosts(ReorderHostRequestDto(hosts=items)))

    def enable_host(self, uuid: str) -> None:
        """Enable a disabled host."""
        sdk_call(self._sdk.hosts_bulk_actions.enable_hosts([UUID(uuid)]))

    def disable_host(self, uuid: str) -> None:
        """Disable a host (subscriptions auto-exclude)."""
        sdk_call(self._sdk.hosts_bulk_actions.disable_hosts([UUID(uuid)]))

    def delete_host(self, uuid: str) -> None:
        """Delete a host entry."""
        sdk_call(self._sdk.hosts.delete_host(uuid))

    # --- Config Profiles ---

    def create_config_profile(self, name: str, config: dict[str, Any]) -> ConfigProfile:
        """Create a new config profile with Xray configuration."""
        body = CreateConfigProfileRequestDto(name=name, config=config)
        resp = sdk_call(self._sdk.config_profiles.create_config_profile(body))
        return config_profile_from_sdk(resp)

    def get_config_profile(self, uuid: str) -> ConfigProfile | None:
        """Get a config profile by UUID. Returns None only if not found."""
        try:
            resp = sdk_call(self._sdk.config_profiles.get_config_profile_by_uuid(uuid))
            return config_profile_from_sdk(resp)
        except RemnawaveNotFoundError:
            return None

    def list_config_profiles(self) -> list[ConfigProfile]:
        """List all config profiles."""
        resp = sdk_call(self._sdk.config_profiles.get_config_profiles())
        # SDK 2.7.x: GetAllConfigProfilesResponseDto.config_profiles
        # (Pydantic snake_case attribute, not the camelCase serialization alias)
        items = getattr(resp, "config_profiles", []) or []
        return [config_profile_from_sdk(profile) for profile in items]

    def find_config_profile_by_name(self, name: str) -> ConfigProfile | None:
        """Find a config profile by name. Returns None if not found."""
        for profile in self.list_config_profiles():
            if profile.name == name:
                return profile
        return None

    # --- Inbounds ---

    def list_inbounds(self) -> list[Inbound]:
        """List all inbounds across config profiles."""
        resp = sdk_call(self._sdk.inbounds.get_all_inbounds())
        inbounds_list = getattr(resp, "inbounds", []) or []
        return [inbound_from_sdk(ib) for ib in inbounds_list]

    # --- Keygen ---

    def get_node_secret_key(self) -> str:
        """Fetch the node mTLS secret bundle from the keygen controller.

        SDK 2.7.x: GetPubKeyResponseDto.pub_key (snake_case attribute, with
        camelCase serialization alias). Reading ``getattr(resp, "pubKey", ...)``
        returns the default empty string and silently breaks node container
        deployment for re-registers.
        """
        resp = sdk_call(self._sdk.keygen.generate_key())
        return str(getattr(resp, "pub_key", "") or "")

    # --- Subscriptions ---

    def get_subscription_url(self, short_uuid: str) -> str:
        """Build the subscription URL for a user."""
        return f"{self._base}/api/sub/{quote(short_uuid, safe='')}"

    # --- Auth (for initial setup — raw httpx, no SDK instance yet) ---

    @classmethod
    def _auth_request(
        cls,
        base_url: str,
        path: str,
        username: str,
        password: str,
        *,
        accepted_codes: tuple[int, ...] = (200,),
        error_label: str = "auth",
        timeout: int = 30,
    ) -> str:
        """Shared auth helper — POST credentials, extract token.

        Both ``login`` and ``register_admin`` are thin wrappers around this.
        """
        import httpx

        url = f"{base_url.rstrip('/')}{path}"
        try:
            resp = httpx.post(
                url,
                json={"username": username, "password": password},
                timeout=timeout,
                verify=False,
            )
        except httpx.ConnectError as e:
            raise RemnawaveNetworkError(
                f"Cannot connect to panel at {base_url}",
                hint=f"Is the panel running? {e}",
                category="system",
            ) from e
        except httpx.TimeoutException:
            raise RemnawaveNetworkError(
                f"Panel {error_label} timed out after {timeout}s",
                category="system",
            )
        if resp.status_code not in accepted_codes:
            detail = resp.text[:200] if resp.status_code >= 400 else ""
            msg = f"Panel {error_label} failed ({resp.status_code})"
            if detail:
                msg = f"{msg}: {detail}"
            raise RemnawaveError(
                msg,
                hint="Check panel credentials" if resp.status_code in (401, 403) else "",
                category="user" if resp.status_code in (401, 403) else "system",
            )
        try:
            data = resp.json()
        except (ValueError, TypeError) as e:
            raise RemnawaveError(f"Panel returned invalid JSON during {error_label}", category="system") from e
        if isinstance(data, dict) and "response" in data:
            data = data["response"]
        token = data.get("accessToken", "") or data.get("token", "")
        if not token:
            raise RemnawaveError(f"{error_label.capitalize()} succeeded but no token returned", category="bug")
        return token

    @classmethod
    def login(cls, base_url: str, username: str, password: str, *, timeout: int = 30) -> str:
        """Authenticate and return an auth token."""
        return cls._auth_request(
            base_url,
            "/api/auth/login",
            username,
            password,
            accepted_codes=(200,),
            error_label="login",
            timeout=timeout,
        )

    @classmethod
    def register_admin(cls, base_url: str, username: str, password: str, *, timeout: int = 30) -> str:
        """Register the initial admin user during setup."""
        return cls._auth_request(
            base_url,
            "/api/auth/register",
            username,
            password,
            accepted_codes=(200, 201),
            error_label="registration",
            timeout=timeout,
        )
