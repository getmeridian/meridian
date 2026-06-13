"""Protocol/inbound type definitions — single source of truth.

Adding a new protocol requires:
1. Create a Protocol subclass below
2. Add an entry to the PROTOCOLS dict (and PROTOCOL_ORDER list)

The rest of the system (client add/remove, output generation) will
pick up the new protocol automatically via the registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from meridian.config import DEFAULT_FINGERPRINT, DEFAULT_SNI

if TYPE_CHECKING:
    from meridian.credentials import ServerCredentials


def _bracket_ipv6(ip: str) -> str:
    """Wrap IPv6 addresses in brackets for URL construction."""
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]"
    return ip


# ---------------------------------------------------------------------------
# Protocol abstraction
# ---------------------------------------------------------------------------


class Protocol(ABC):
    """Base class for proxy protocols.

    Each Protocol knows how to:
    - Build a connection URL for a given client UUID
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Protocol key (e.g., 'reality', 'wss', 'xhttp')."""
        ...

    @property
    def display_label(self) -> str:
        """Human-readable label for output (e.g., 'Primary', 'CDN Backup')."""
        return self.key.upper()

    @abstractmethod
    def build_url(self, uuid: str, name: str, **kwargs: Any) -> str:
        """Build a connection URL for this protocol.

        Args:
            uuid: Client UUID.
            name: Client display name (used in URL fragment).
            **kwargs: Protocol-specific parameters (ip, port, sni, etc.).

        Returns:
            A complete VLESS/etc URL string.
        """
        ...

    @property
    def requires_domain(self) -> bool:
        """Whether this protocol requires a domain to function."""
        return False

    @property
    def shares_uuid_with(self) -> str | None:
        """Key of another protocol this one shares a UUID with.

        XHTTP shares the Reality UUID for Xray inbound routing, but uses
        standard TLS (nginx-terminated), not Reality security.
        None means this protocol uses its own UUID.
        """
        return None

    @property
    def url_suffix(self) -> str:
        """Suffix appended to client name in the URL fragment (e.g., '-XHTTP')."""
        return ""

    def _build_fragment(self, name: str, server_name: str = "", extra_suffix: str = "") -> str:
        """Build the URL fragment (after #) for a connection URL."""
        base = f"{name} @ {server_name}" if server_name else name
        return f"#{base}{extra_suffix}{self.url_suffix}"

    def _resolve_uuid(self, reality_uuid: str, wss_uuid: str) -> str:
        """Pick the correct UUID for this protocol.

        Reality and protocols sharing Reality's UUID use reality_uuid.
        Protocols with their own UUID (like WSS) use wss_uuid.
        """
        if self.key == "reality" or self.shares_uuid_with is not None:
            return reality_uuid
        return wss_uuid

    def build_url_from_creds(
        self,
        reality_uuid: str,
        wss_uuid: str,
        creds: ServerCredentials,
        name: str,
        *,
        server_name: str = "",
    ) -> str:
        """Build a connection URL using server credentials.

        Each subclass extracts its own parameters from creds and delegates
        to build_url(). Returns empty string if required data is missing
        (e.g., WSS without a domain, XHTTP without a path).
        """
        return ""

    def build_relay_url(
        self,
        reality_uuid: str,
        wss_uuid: str,
        creds: ServerCredentials,
        name: str,
        relay_ip: str,
        relay_port: int = 443,
        *,
        relay_sni: str = "",
        relay_name: str = "",
        server_name: str = "",
    ) -> str:
        """Build a connection URL routed through a relay node.

        Relay URLs substitute the relay's IP/port for the exit server's.
        Returns empty string if the protocol is not available.
        """
        return ""


class RealityProtocol(Protocol):
    """VLESS + Reality + TCP — primary protocol, always present."""

    @property
    def key(self) -> str:
        return "reality"

    @property
    def display_label(self) -> str:
        return "Primary"

    def build_url(self, uuid: str, name: str, **kwargs: Any) -> str:
        ip = kwargs["ip"]
        port = kwargs.get("port", 443)
        sni = kwargs.get("sni", DEFAULT_SNI)
        public_key = kwargs.get("public_key", "")
        short_id = kwargs.get("short_id", "")
        fingerprint = kwargs.get("fingerprint", DEFAULT_FINGERPRINT)
        encryption = kwargs.get("encryption", "none")
        extra_suffix = kwargs.get("extra_suffix", "")
        fragment = self._build_fragment(name, kwargs.get("server_name", ""), extra_suffix)
        return (
            f"vless://{uuid}@{_bracket_ipv6(ip)}:{port}"
            f"?encryption={encryption}&flow=xtls-rprx-vision"
            f"&security=reality&sni={sni}&fp={fingerprint}"
            f"&pbk={public_key}&sid={short_id}"
            f"&type=tcp&headerType=none"
            f"{fragment}"
        )

    def build_url_from_creds(
        self, reality_uuid: str, wss_uuid: str, creds: ServerCredentials, name: str, *, server_name: str = ""
    ) -> str:
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        return self.build_url(
            uuid,
            name,
            ip=creds.server.ip or "",
            sni=creds.server.sni or DEFAULT_SNI,
            public_key=creds.reality.public_key or "",
            short_id=creds.reality.short_id or "",
            encryption=creds.reality.encryption_key or "none",
            server_name=server_name,
        )

    def build_relay_url(
        self,
        reality_uuid: str,
        wss_uuid: str,
        creds: ServerCredentials,
        name: str,
        relay_ip: str,
        relay_port: int = 443,
        *,
        relay_sni: str = "",
        relay_name: str = "",
        server_name: str = "",
    ) -> str:
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        via = f"-via-{relay_name}" if relay_name else f"-via-{relay_ip}"
        return self.build_url(
            uuid,
            name,
            ip=relay_ip,
            port=relay_port,
            sni=relay_sni or creds.server.sni or DEFAULT_SNI,
            public_key=creds.reality.public_key or "",
            short_id=creds.reality.short_id or "",
            encryption=creds.reality.encryption_key or "none",
            server_name=server_name,
            extra_suffix=via,
        )


class XHTTPProtocol(Protocol):
    """VLESS + XHTTP — enhanced stealth transport behind nginx."""

    @property
    def key(self) -> str:
        return "xhttp"

    @property
    def display_label(self) -> str:
        return "XHTTP"

    @property
    def shares_uuid_with(self) -> str | None:
        return "reality"

    @property
    def url_suffix(self) -> str:
        return "-XHTTP"

    def build_url(self, uuid: str, name: str, **kwargs: Any) -> str:
        ip = kwargs["ip"]
        port = kwargs.get("port", 443)
        xhttp_path = kwargs.get("xhttp_path", "")
        domain = kwargs.get("domain", "")
        fingerprint = kwargs.get("fingerprint", DEFAULT_FINGERPRINT)
        extra_suffix = kwargs.get("extra_suffix", "")
        # sni kwarg overrides the derived SNI (used by relay URLs)
        sni_host = kwargs.get("sni") or domain or _bracket_ipv6(ip)
        # connect_host overrides the @host in the URL (for relay connections)
        connect_host = kwargs.get("connect_host", sni_host)
        fragment = self._build_fragment(name, kwargs.get("server_name", ""), extra_suffix)
        return (
            f"vless://{uuid}@{connect_host}:{port}"
            f"?encryption=none&security=tls&sni={sni_host}&fp={fingerprint}"
            f"&type=xhttp&path=%2F{xhttp_path}{fragment}"
        )

    def build_url_from_creds(
        self, reality_uuid: str, wss_uuid: str, creds: ServerCredentials, name: str, *, server_name: str = ""
    ) -> str:
        xhttp_path = creds.xhttp.xhttp_path or ""
        if not xhttp_path:
            return ""
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        return self.build_url(
            uuid,
            name,
            ip=creds.server.ip or "",
            xhttp_path=xhttp_path,
            domain=creds.server.domain or "",
            server_name=server_name,
        )

    def build_relay_url(
        self,
        reality_uuid: str,
        wss_uuid: str,
        creds: ServerCredentials,
        name: str,
        relay_ip: str,
        relay_port: int = 443,
        *,
        relay_sni: str = "",
        relay_name: str = "",
        server_name: str = "",
    ) -> str:
        xhttp_path = creds.xhttp.xhttp_path or ""
        if not xhttp_path:
            return ""
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        via = f"-via-{relay_name}" if relay_name else f"-via-{relay_ip}"
        return self.build_url(
            uuid,
            name,
            ip=creds.server.ip or "",
            port=relay_port,
            xhttp_path=xhttp_path,
            domain=creds.server.domain or "",
            sni=relay_sni,
            connect_host=_bracket_ipv6(relay_ip),
            server_name=server_name,
            extra_suffix=via,
        )


class WSSProtocol(Protocol):
    """VLESS + WSS — CDN fallback via nginx/Cloudflare."""

    @property
    def key(self) -> str:
        return "wss"

    @property
    def display_label(self) -> str:
        return "CDN Backup"

    @property
    def requires_domain(self) -> bool:
        return True

    @property
    def url_suffix(self) -> str:
        return "-WSS"

    def build_url(self, uuid: str, name: str, **kwargs: Any) -> str:
        domain = kwargs["domain"]
        port = kwargs.get("port", 443)
        ws_path = kwargs.get("ws_path", "")
        # sni kwarg overrides the domain for TLS SNI (used by relay URLs)
        sni = kwargs.get("sni") or domain
        # connect_host overrides the @host in the URL (for relay connections)
        connect_host = kwargs.get("connect_host", domain)
        extra_suffix = kwargs.get("extra_suffix", "")
        fragment = self._build_fragment(name, kwargs.get("server_name", ""), extra_suffix)
        return (
            f"vless://{uuid}@{connect_host}:{port}"
            f"?encryption=none&security=tls&sni={sni}"
            f"&type=ws&host={domain}&path=%2F{ws_path}"
            f"{fragment}"
        )

    def build_url_from_creds(
        self, reality_uuid: str, wss_uuid: str, creds: ServerCredentials, name: str, *, server_name: str = ""
    ) -> str:
        domain = creds.server.domain or ""
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        if not domain or not uuid:
            return ""
        return self.build_url(
            uuid,
            name,
            domain=domain,
            ws_path=creds.wss.ws_path or "",
            server_name=server_name,
        )

    def build_relay_url(
        self,
        reality_uuid: str,
        wss_uuid: str,
        creds: ServerCredentials,
        name: str,
        relay_ip: str,
        relay_port: int = 443,
        *,
        relay_sni: str = "",
        relay_name: str = "",
        server_name: str = "",
    ) -> str:
        domain = creds.server.domain or ""
        uuid = self._resolve_uuid(reality_uuid, wss_uuid)
        ws_path = creds.wss.ws_path or ""
        if not domain or not uuid or not ws_path:
            return ""
        via = f"-via-{relay_name}" if relay_name else f"-via-{relay_ip}"
        return self.build_url(
            uuid,
            name,
            domain=domain,
            port=relay_port,
            ws_path=ws_path,
            sni=relay_sni,
            connect_host=relay_ip,
            server_name=server_name,
            extra_suffix=via,
        )


class Hysteria2Protocol(Protocol):
    """Hysteria2 — experimental UDP/443 fallback for high-latency networks.

    Uses QUIC/UDP on port 443, coexisting with TCP/443 (nginx/Reality/XHTTP).
    Opt-in only — Russia suppresses unidentified UDP traffic.
    """

    @property
    def key(self) -> str:
        return "hysteria2"

    @property
    def display_label(self) -> str:
        return "UDP (Experimental)"

    @property
    def requires_domain(self) -> bool:
        return False

    @property
    def url_suffix(self) -> str:
        return "-HY2"

    def build_url(self, uuid: str, name: str, **kwargs: Any) -> str:
        ip = kwargs["ip"]
        port = kwargs.get("port", 443)
        sni = kwargs.get("sni", "")
        extra_suffix = kwargs.get("extra_suffix", "")
        fragment = self._build_fragment(name, kwargs.get("server_name", ""), extra_suffix)
        return f"hysteria2://{uuid}@{_bracket_ipv6(ip)}:{port}?sni={sni}&insecure=1{fragment}"


# ---------------------------------------------------------------------------
# Protocol registry
# ---------------------------------------------------------------------------

# Dict for O(1) lookup, ordered: Reality first (primary), then XHTTP, then WSS.
PROTOCOLS: dict[str, Protocol] = {
    "reality": RealityProtocol(),
    "xhttp": XHTTPProtocol(),
    "wss": WSSProtocol(),
}

# Explicit ordering for iteration (dict preserves insertion order in Python 3.7+,
# but this makes the intent explicit and allows reordering without changing keys).
PROTOCOL_ORDER: list[str] = ["reality", "xhttp", "wss"]

# Experimental protocols — not in PROTOCOLS/PROTOCOL_ORDER by default.
# Enabled per-node via cluster.yml hysteria2 flag.
EXPERIMENTAL_PROTOCOLS: dict[str, Protocol] = {
    "hysteria2": Hysteria2Protocol(),
}


def get_protocol(key: str) -> Protocol | None:
    """Find a protocol by key (e.g., 'reality', 'wss', 'xhttp', 'hysteria2')."""
    return PROTOCOLS.get(key) or EXPERIMENTAL_PROTOCOLS.get(key)
