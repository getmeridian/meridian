"""Integration test: 3x-ui API round-trip.

Requires a running 3x-ui container (docker-compose.test.yml).
Skipped automatically when the panel is not reachable.

Run locally:
    docker compose -f docker-compose.test.yml up -d
    make test  # or: pytest tests/test_integration_3xui.py -v

Run in CI:
    The 'integration' job starts the container as a service.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

PANEL_URL = "http://localhost:2053"
DEFAULT_USER = "admin"
DEFAULT_PASS = "admin"


def _panel_ready() -> bool:
    """Check if the 3x-ui panel is reachable (fresh panel returns 200 or 404)."""
    try:
        req = urllib.request.Request(f"{PANEL_URL}/", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status in (200, 301, 302, 404)
    except urllib.error.HTTPError as e:
        return e.code in (200, 301, 302, 404)
    except Exception:
        return False


# Skip entire module if panel is not running
pytestmark = pytest.mark.skipif(not _panel_ready(), reason="3x-ui panel not running")


class ThreeXUIClient:
    """Minimal 3x-ui API client for testing."""

    def __init__(self, base_url: str = PANEL_URL) -> None:
        self.base_url = base_url
        self.cookie: str = ""

    def login(self, username: str = DEFAULT_USER, password: str = DEFAULT_PASS) -> dict:
        """Login and store session cookie. Uses form-urlencoded (required by 3x-ui)."""
        data = urllib.parse.urlencode({"username": username, "password": password}).encode()
        req = urllib.request.Request(f"{self.base_url}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = urllib.request.urlopen(req, timeout=10)
        self.cookie = resp.headers.get("Set-Cookie", "")
        body = json.loads(resp.read())
        assert body.get("success"), f"Login failed: {body}"
        return body

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        """Make an authenticated API call."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        if data:
            req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())

    def list_inbounds(self) -> list[dict]:
        result = self._api("GET", "/panel/api/inbounds/list")
        assert result.get("success"), f"List inbounds failed: {result}"
        return result.get("obj", [])

    def add_inbound(self, inbound: dict) -> dict:
        result = self._api("POST", "/panel/api/inbounds/add", inbound)
        assert result.get("success"), f"Add inbound failed: {result}"
        return result

    def delete_inbound(self, inbound_id: int) -> dict:
        result = self._api("POST", f"/panel/api/inbounds/del/{inbound_id}")
        assert result.get("success"), f"Delete inbound failed: {result}"
        return result

    def add_client(self, inbound_id: int, client_settings: str) -> dict:
        result = self._api(
            "POST",
            "/panel/api/inbounds/addClient",
            {
                "id": inbound_id,
                "settings": client_settings,
            },
        )
        assert result.get("success"), f"Add client failed: {result}"
        return result

    def delete_client(self, inbound_id: int, client_uuid: str) -> dict:
        result = self._api("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}")
        assert result.get("success"), f"Delete client failed: {result}"
        return result

    def update_settings(self, settings: dict) -> dict:
        result = self._api("POST", "/panel/setting/update", settings)
        assert result.get("success"), f"Update settings failed: {result}"
        return result

    def update_user(self, old_user: str, old_pass: str, new_user: str, new_pass: str) -> dict:
        result = self._api(
            "POST",
            "/panel/setting/updateUser",
            {
                "oldUsername": old_user,
                "oldPassword": old_pass,
                "newUsername": new_user,
                "newPassword": new_pass,
            },
        )
        assert result.get("success"), f"Update user failed: {result}"
        return result

    def get_settings(self) -> dict:
        result = self._api("POST", "/panel/setting/all")
        assert result.get("success"), f"Get settings failed: {result}"
        return result


@pytest.fixture(scope="module")
def client() -> ThreeXUIClient:
    """Create and authenticate a 3x-ui API client."""
    c = ThreeXUIClient()
    c.login()
    return c


class TestLogin:
    def test_login_default_credentials(self) -> None:
        c = ThreeXUIClient()
        result = c.login()
        assert result["success"]
        assert c.cookie  # session cookie must be set

    def test_login_wrong_password(self) -> None:
        # 3x-ui returns success=false for wrong creds, doesn't raise HTTP error
        data = urllib.parse.urlencode({"username": "admin", "password": "wrong"}).encode()
        req = urllib.request.Request(f"{PANEL_URL}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = urllib.request.urlopen(req, timeout=10)
        body = json.loads(resp.read())
        assert not body["success"]


class TestInboundCRUD:
    """Test create → list → verify → delete round-trip."""

    def test_reality_inbound_round_trip(self, client: ThreeXUIClient) -> None:
        """Simulate the exact Reality inbound creation the playbook does."""
        test_uuid = str(uuid.uuid4())
        test_port = 19443  # unlikely to conflict

        # Settings/streamSettings/sniffing must be JSON strings (not objects)
        settings = json.dumps(
            {
                "clients": [
                    {
                        "id": test_uuid,
                        "flow": "xtls-rprx-vision",
                        "email": "reality-test",
                        "limitIp": 2,
                        "totalGB": 0,
                        "expiryTime": 0,
                        "enable": True,
                        "tgId": "",
                        "subId": "",
                        "reset": 0,
                    }
                ],
                "decryption": "none",
                "fallbacks": [],
            }
        )

        stream_settings = json.dumps(
            {
                "network": "tcp",
                "security": "reality",
                "externalProxy": [],
                "realitySettings": {
                    "show": False,
                    "xver": 0,
                    "dest": "www.cloudflare.com:443",
                    "serverNames": ["www.cloudflare.com"],
                    "privateKey": "WBNp7SHzGMaqp6ohXMfJHUyBMWHoeHMflVPaaxdtRHo",
                    "minClient": "",
                    "maxClient": "",
                    "maxTimediff": 0,
                    "shortIds": ["abcd1234"],
                    "settings": {
                        "publicKey": "K6JYbz4MflVPaaxdtRHoWBNp7SHzGMaqp6ohXMfJHUy",
                        "fingerprint": "chrome",
                        "serverName": "",
                        "spiderX": "/abcd1234",
                    },
                },
                "tcpSettings": {
                    "acceptProxyProtocol": False,
                    "header": {"type": "none"},
                },
            }
        )

        sniffing = json.dumps(
            {
                "enabled": True,
                "destOverride": ["http", "tls", "quic", "fakedns"],
                "metadataOnly": False,
                "routeOnly": False,
            }
        )

        inbound = {
            "remark": "VLESS-Reality-Test",
            "enable": True,
            "port": test_port,
            "protocol": "vless",
            "expiryTime": 0,
            "total": 0,
            "settings": settings,
            "streamSettings": stream_settings,
            "sniffing": sniffing,
        }

        # Create
        result = client.add_inbound(inbound)
        assert result["success"]
        inbound_id = result["obj"]["id"]

        try:
            # List and verify
            inbounds = client.list_inbounds()
            found = [i for i in inbounds if i["remark"] == "VLESS-Reality-Test"]
            assert len(found) == 1, f"Expected 1 inbound, found {len(found)}"

            created = found[0]
            assert created["port"] == test_port
            assert created["protocol"] == "vless"

            # Verify settings survived JSON round-trip (the production bug was here)
            parsed_settings = json.loads(created["settings"])
            assert len(parsed_settings["clients"]) == 1
            assert parsed_settings["clients"][0]["id"] == test_uuid
            assert parsed_settings["clients"][0]["flow"] == "xtls-rprx-vision"
            assert parsed_settings["clients"][0]["email"] == "reality-test"
            assert parsed_settings["decryption"] == "none"

            parsed_stream = json.loads(created["streamSettings"])
            assert parsed_stream["network"] == "tcp"
            assert parsed_stream["security"] == "reality"
            assert parsed_stream["realitySettings"]["dest"] == "www.cloudflare.com:443"

        finally:
            # Cleanup
            client.delete_inbound(inbound_id)

        # Verify deletion
        after = client.list_inbounds()
        assert not any(i["remark"] == "VLESS-Reality-Test" for i in after)


class TestClientManagement:
    """Test add/remove client on an inbound."""

    def test_add_remove_client(self, client: ThreeXUIClient) -> None:
        # Create a minimal inbound to host clients
        base_uuid = str(uuid.uuid4())
        inbound = {
            "remark": "Client-Test-Inbound",
            "enable": True,
            "port": 19444,
            "protocol": "vless",
            "expiryTime": 0,
            "total": 0,
            "settings": json.dumps(
                {
                    "clients": [
                        {
                            "id": base_uuid,
                            "flow": "xtls-rprx-vision",
                            "email": "reality-base",
                            "limitIp": 2,
                            "totalGB": 0,
                            "expiryTime": 0,
                            "enable": True,
                            "tgId": "",
                            "subId": "",
                            "reset": 0,
                        }
                    ],
                    "decryption": "none",
                    "fallbacks": [],
                }
            ),
            "streamSettings": json.dumps(
                {
                    "network": "tcp",
                    "security": "none",
                    "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
                }
            ),
            "sniffing": json.dumps({"enabled": False, "destOverride": []}),
        }

        result = client.add_inbound(inbound)
        inbound_id = result["obj"]["id"]

        try:
            # Add a new client
            new_uuid = str(uuid.uuid4())
            client_settings = json.dumps(
                {
                    "clients": [
                        {
                            "id": new_uuid,
                            "flow": "xtls-rprx-vision",
                            "email": "reality-alice",
                            "limitIp": 2,
                            "totalGB": 0,
                            "expiryTime": 0,
                            "enable": True,
                            "tgId": "",
                            "subId": "",
                            "reset": 0,
                        }
                    ]
                }
            )
            client.add_client(inbound_id, client_settings)

            # Verify client was added
            inbounds = client.list_inbounds()
            found = [i for i in inbounds if i["id"] == inbound_id][0]
            clients = json.loads(found["settings"])["clients"]
            assert len(clients) == 2
            emails = {c["email"] for c in clients}
            assert "reality-base" in emails
            assert "reality-alice" in emails

            # Remove client by UUID (NOT email — email silently succeeds but doesn't delete)
            client.delete_client(inbound_id, new_uuid)

            # Verify client was removed
            inbounds = client.list_inbounds()
            found = [i for i in inbounds if i["id"] == inbound_id][0]
            clients = json.loads(found["settings"])["clients"]
            assert len(clients) == 1
            assert clients[0]["email"] == "reality-base"

        finally:
            client.delete_inbound(inbound_id)


class TestSettings:
    """Test settings update — mirrors the playbook's panel configuration."""

    def test_update_web_base_path(self, client: ThreeXUIClient) -> None:
        """Update webBasePath and verify it persists."""
        test_path = "testpath123"

        # Read current settings to avoid clobbering
        current = client.get_settings()
        assert current["success"]

        # Update just webBasePath (must send all fields — 3x-ui replaces the whole object)
        client.update_settings(
            {
                "webListen": "",
                "webDomain": "",
                "webPort": 2053,
                "webCertFile": "",
                "webKeyFile": "",
                "webBasePath": f"/{test_path}/",
                "sessionMaxAge": 60,
                "expireDiff": 0,
                "trafficDiff": 0,
                "remarkModel": "-full",
                "tgBotEnable": False,
                "tgBotToken": "",
                "tgBotChatId": "",
                "tgRunTime": "@daily",
                "tgBotBackup": False,
                "tgBotLoginNotify": True,
                "tgCpu": 80,
                "subEnable": False,
                "subListen": "",
                "subPort": 2096,
                "subPath": "/sub/",
                "subDomain": "",
                "subCertFile": "",
                "subKeyFile": "",
                "subUpdates": 12,
                "subEncrypt": True,
                "subShowInfo": False,
                "subURI": "",
                "subJsonPath": "/json/",
                "subJsonURI": "",
                "subJsonFragment": "",
                "subJsonMux": "",
                "subJsonRules": "",
                "datepicker": "gregorian",
                "pageSize": 50,
                "loginSecurity": True,
            }
        )

        # Verify — read settings back
        updated = client.get_settings()
        assert updated["obj"]["webBasePath"] == f"/{test_path}/"

        # Reset to default so other tests aren't affected
        client.update_settings(
            {
                "webListen": "",
                "webDomain": "",
                "webPort": 2053,
                "webCertFile": "",
                "webKeyFile": "",
                "webBasePath": "/",
                "sessionMaxAge": 60,
                "expireDiff": 0,
                "trafficDiff": 0,
                "remarkModel": "-full",
                "tgBotEnable": False,
                "tgBotToken": "",
                "tgBotChatId": "",
                "tgRunTime": "@daily",
                "tgBotBackup": False,
                "tgBotLoginNotify": True,
                "tgCpu": 80,
                "subEnable": False,
                "subListen": "",
                "subPort": 2096,
                "subPath": "/sub/",
                "subDomain": "",
                "subCertFile": "",
                "subKeyFile": "",
                "subUpdates": 12,
                "subEncrypt": True,
                "subShowInfo": False,
                "subURI": "",
                "subJsonPath": "/json/",
                "subJsonURI": "",
                "subJsonFragment": "",
                "subJsonMux": "",
                "subJsonRules": "",
                "datepicker": "gregorian",
                "pageSize": 50,
                "loginSecurity": True,
            }
        )


class TestCredentialChange:
    """Test credential update — the playbook changes admin/admin to random creds."""

    def test_change_and_relogin(self) -> None:
        """Change credentials, login with new ones, then restore defaults."""
        c = ThreeXUIClient()
        c.login("admin", "admin")

        new_user = "testuser"
        new_pass = "testpass123"

        # Change credentials
        c.update_user("admin", "admin", new_user, new_pass)

        # Login with new credentials
        c2 = ThreeXUIClient()
        c2.login(new_user, new_pass)
        assert c2.cookie

        # Old credentials should fail
        data = urllib.parse.urlencode({"username": "admin", "password": "admin"}).encode()
        req = urllib.request.Request(f"{PANEL_URL}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = urllib.request.urlopen(req, timeout=10)
        body = json.loads(resp.read())
        assert not body["success"]

        # Restore default credentials
        c2.update_user(new_user, new_pass, "admin", "admin")

        # Verify restore worked
        c3 = ThreeXUIClient()
        c3.login("admin", "admin")
        assert c3.cookie
