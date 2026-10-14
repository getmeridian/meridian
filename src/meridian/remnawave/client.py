"""Typed synchronous facade over the Remnawave REST API."""

from __future__ import annotations

import logging
import random
import ssl
import time
from typing import Any
from urllib.parse import quote

import httpx

from .control_plane import ControlPlaneMixin
from .models import (
    ConfigProfile,
    Host,
    HostSecurityLayer,
    Node,
    NodeCredentials,
    User,
    is_user_id,
    parse_config_profile,
    parse_host,
    parse_node,
    parse_user,
    response_items,
)
from .runtime import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)

logger = logging.getLogger("meridian.api")

_EMPTY_BODY_STATUSES = frozenset({202, 204})
_USERS_PAGE_SIZE = 1000  # Remnawave rejects larger pages


def _segment(value: str) -> str:
    return quote(str(value), safe="")


class MeridianPanel(ControlPlaneMixin):
    """Remnawave API client — sync facade over raw REST calls.

    Every request goes through ``_request`` (retry, typed errors, envelope
    unwrapping) and every payload is parsed into a stable model in
    ``models.py`` before it leaves this package.

    Domain groups (natural extraction boundaries):
      - Auth (login, register_admin) — classmethods, no client instance
      - System — version metadata and reachability
      - Users — read, enable/disable, delete Meridian clients (= Remnawave users, numeric ``id``)
      - Nodes — register, list, enable/disable, delete proxy nodes
      - Hosts — direct addresses and relay entries in subscriptions
      - Config Profiles — Xray configuration templates
      - Internal Squads — user-to-inbound access control groups
      - Keygen — node mTLS secret bundles

    Usage:
        panel = MeridianPanel("https://panel.example.com", "jwt-token")
        user = panel.get_user("alice")
        nodes = panel.list_nodes()
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: float = 30,
        max_retries: int = 5,
        verify: bool | str | ssl.SSLContext = True,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        authorization = api_token if api_token.startswith("Bearer ") else f"Bearer {api_token}"
        self._client = httpx.Client(
            base_url=self._base + "/",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=verify,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> MeridianPanel:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # --- Low-level raw request ---

    def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        """Make an API request with retry logic.

        Returns the unwrapped ``response`` envelope, or ``None`` for empty
        2xx bodies (Remnawave 3.x answers DELETE/bulk calls with 204 and
        node restarts with 202).
        """
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
                if resp.status_code in _EMPTY_BODY_STATUSES or not resp.content.strip():
                    return None
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

    # --- System ---

    def get_version(self) -> str:
        """Return the running Remnawave backend version (``/api/system/metadata``)."""
        data = self._request("GET", "/api/system/metadata")
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str) or not version.strip():
            raise RemnawaveError(
                "Panel metadata did not include a version",
                hint="Is this a Remnawave panel? GET /api/system/metadata returned no version.",
                category="system",
            )
        return version.strip()

    def ping(self) -> bool:
        """Check if the panel is reachable and authenticated."""
        try:
            self.get_version()
            return True
        except RemnawaveAuthError:
            raise
        except (RemnawaveError, httpx.HTTPError, OSError):
            return False

    # --- Users (= Meridian clients) ---

    def get_user(self, username: str) -> User | None:
        """Find a user by username. Returns None only if not found (404)."""
        try:
            data = self._request("GET", f"/api/users/by-username/{_segment(username)}")
        except RemnawaveNotFoundError:
            return None
        return parse_user(data)

    def get_user_by_id(self, user_id: str) -> User | None:
        """Find a user by numeric id. Returns None if not found.

        Pre-3.x bindings stored the removed user ``uuid``; such identities
        are not addressable any more, so they resolve to None and callers
        fall back to the username.
        """
        if not is_user_id(user_id):
            return None
        try:
            data = self._request("GET", f"/api/users/{_segment(user_id)}")
        except RemnawaveNotFoundError:
            return None
        return parse_user(data)

    def list_users(self) -> list[User]:
        """List all users."""
        all_users: list[User] = []
        start = 0
        while True:
            data = self._request("GET", "/api/users", params={"start": start, "size": _USERS_PAGE_SIZE})
            users_list = response_items(data, "users")
            if not users_list:
                break
            all_users.extend(parse_user(u) for u in users_list)
            if len(users_list) < _USERS_PAGE_SIZE:
                break
            start += _USERS_PAGE_SIZE
        return all_users

    def delete_user(self, user_id: str) -> bool:
        """Delete a user by id. Returns False only if not found."""
        return self._delete(f"/api/users/{_segment(user_id)}")

    def enable_user(self, user_id: str) -> None:
        """Enable a disabled user."""
        self._post(f"/api/users/{_segment(user_id)}/actions/enable")

    def disable_user(self, user_id: str) -> None:
        """Disable a user."""
        self._post(f"/api/users/{_segment(user_id)}/actions/disable")

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
        data = self._post(
            "/api/nodes",
            json={
                "name": name,
                "address": address,
                "port": port,
                "countryCode": country_code,
                "configProfile": {
                    "activeConfigProfileUuid": config_profile_uuid,
                    "activeInbounds": inbound_uuids or [],
                },
            },
        )
        node = parse_node(data)
        return NodeCredentials(uuid=node.uuid, secret_key=self.get_node_secret_key(), _raw=node._raw)

    def list_nodes(self) -> list[Node]:
        """List all registered nodes."""
        return [parse_node(n) for n in response_items(self._request("GET", "/api/nodes"), "nodes")]

    def find_node_by_address(self, address: str) -> Node | None:
        """Find a node by its address. Returns None if not found."""
        for node in self.list_nodes():
            if node.address == address:
                return node
        return None

    def get_node(self, uuid: str) -> Node | None:
        """Get a node by UUID. Returns None only if not found (404)."""
        try:
            data = self._request("GET", f"/api/nodes/{_segment(uuid)}")
        except RemnawaveNotFoundError:
            return None
        return parse_node(data)

    def enable_node(self, uuid: str) -> None:
        """Enable a disabled node."""
        self._post(f"/api/nodes/{_segment(uuid)}/actions/enable")

    def disable_node(self, uuid: str) -> None:
        """Disable a node."""
        self._post(f"/api/nodes/{_segment(uuid)}/actions/disable")

    def delete_node(self, uuid: str) -> None:
        """Deregister a node."""
        self._request("DELETE", f"/api/nodes/{_segment(uuid)}")

    def update_node_name(self, uuid: str, name: str) -> None:
        """Update a node's name in the panel."""
        self._patch("/api/nodes", json={"uuid": uuid, "name": name})

    def update_node_country(self, uuid: str, country_code: str) -> None:
        """Set the country flag the panel shows for a node."""
        self._patch("/api/nodes", json={"uuid": uuid, "countryCode": country_code})

    def update_node_address(self, uuid: str, address: str, port: int) -> None:
        """Point a node record at another server (a moved exit keeps its Node)."""
        self._patch("/api/nodes", json={"uuid": uuid, "address": address, "port": port})

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
        security_layer: HostSecurityLayer = "DEFAULT",
        is_disabled: bool = False,
        tags: list[str] | None = None,
        is_hidden: bool = False,
        xray_json_template_uuid: str = "",
        exclude_from_subscription_types: list[str] | None = None,
    ) -> Host:
        """Create a host entry (direct address or relay).

        Nullable and complete Host fields remain under Meridian's explicit
        wire control. Reality inherits from the Inbound
        when the Host security layer is DEFAULT.
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
        if tags:
            body["tags"] = tags
        if is_hidden:
            body["isHidden"] = True
        if xray_json_template_uuid:
            body["xrayJsonTemplateUuid"] = xray_json_template_uuid
        if exclude_from_subscription_types:
            body["excludeFromSubscriptionTypes"] = exclude_from_subscription_types
        data = self._post("/api/hosts", json=body)
        return parse_host(data)

    def list_hosts(self) -> list[Host]:
        """List all host entries."""
        return [parse_host(h) for h in response_items(self._request("GET", "/api/hosts"), "hosts")]

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
        security_layer: HostSecurityLayer = "DEFAULT",
        is_disabled: bool = False,
        tags: list[str] | None = None,
        is_hidden: bool = False,
        xray_json_template_uuid: str = "",
        exclude_from_subscription_types: list[str] | None = None,
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
            "tags": tags or [],
            "isHidden": is_hidden,
            "xrayJsonTemplateUuid": xray_json_template_uuid or None,
            "excludeFromSubscriptionTypes": exclude_from_subscription_types or [],
        }
        data = self._patch("/api/hosts", json=body)
        return parse_host(data)

    def set_host_positions(self, positions: dict[str, int]) -> None:
        """Set each listed Host's viewPosition; subscriptions list Hosts by it, ascending."""
        items = [{"uuid": host_uuid, "viewPosition": position} for host_uuid, position in positions.items()]
        self._post("/api/hosts/actions/reorder", json={"hosts": items})

    def delete_host(self, uuid: str) -> None:
        """Delete a host entry."""
        self._request("DELETE", f"/api/hosts/{_segment(uuid)}")

    # --- Config Profiles ---

    def create_config_profile(self, name: str, config: dict[str, Any]) -> ConfigProfile:
        """Create a new config profile with Xray configuration."""
        return parse_config_profile(self._post("/api/config-profiles", json={"name": name, "config": config}))

    def get_config_profile(self, uuid: str) -> ConfigProfile | None:
        """Get a config profile by UUID. Returns None only if not found."""
        try:
            data = self._request("GET", f"/api/config-profiles/{_segment(uuid)}")
        except RemnawaveNotFoundError:
            return None
        return parse_config_profile(data)

    def list_config_profiles(self) -> list[ConfigProfile]:
        """List all config profiles."""
        data = self._request("GET", "/api/config-profiles")
        return [parse_config_profile(profile) for profile in response_items(data, "configProfiles")]

    # --- Keygen ---

    def get_node_secret_key(self) -> str:
        """Fetch the node mTLS secret bundle from the keygen controller.

        Remnawave 3.x names it ``secretKey``; 2.x called it ``pubKey``.
        Callers must treat an empty value as a failed node deployment.
        """
        data = self._request("GET", "/api/keygen")
        raw = data if isinstance(data, dict) else {}
        return str(raw.get("secretKey") or raw.get("pubKey") or "")

    def generate_x25519_keypairs(self) -> list[tuple[str, str]]:
        """Return fresh ``(private_key, public_key)`` pairs from the panel's tools controller."""
        data = self._request("GET", "/api/system/tools/x25519/generate")
        raw = data.get("keypairs") if isinstance(data, dict) else None
        return [
            (str(item.get("privateKey") or ""), str(item.get("publicKey") or ""))
            for item in raw or []
            if isinstance(item, dict)
        ]

    # --- Auth (for initial setup — no authenticated client yet) ---

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
        timeout: float = 30,
        verify: bool | str | ssl.SSLContext = True,
    ) -> str:
        """Shared auth helper — POST credentials, extract token.

        Both ``login`` and ``register_admin`` are thin wrappers around this.
        """
        url = f"{base_url.rstrip('/')}{path}"
        try:
            resp = httpx.post(
                url,
                json={"username": username, "password": password},
                timeout=timeout,
                verify=verify,
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
            if resp.status_code in (401, 403):
                raise RemnawaveAuthError(msg, hint="Check panel credentials", category="user")
            raise RemnawaveError(msg, category="system")
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
    def login(
        cls,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30,
        verify: bool | str | ssl.SSLContext = True,
    ) -> str:
        """Authenticate and return an auth token."""
        return cls._auth_request(
            base_url,
            "/api/auth/login",
            username,
            password,
            accepted_codes=(200,),
            error_label="login",
            timeout=timeout,
            verify=verify,
        )

    @classmethod
    def register_admin(
        cls,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30,
        verify: bool | str | ssl.SSLContext = True,
    ) -> str:
        """Register the initial admin user during setup."""
        return cls._auth_request(
            base_url,
            "/api/auth/register",
            username,
            password,
            accepted_codes=(200, 201),
            error_label="registration",
            timeout=timeout,
            verify=verify,
        )
