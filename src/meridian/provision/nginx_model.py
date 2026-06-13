"""Typed nginx configuration model.

Pure data construction — no I/O, no SSH. The provisioner step renders
this model to a string and writes it via conn.put_text().
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Stream layer (SNI routing — sits on port 443, no TLS termination)
# ---------------------------------------------------------------------------


@dataclass
class NginxUpstream:
    """A named upstream block with a single backend server."""

    name: str
    server: str  # e.g. "127.0.0.1:10001"


@dataclass
class NginxStreamMap:
    """SNI-based stream routing entry (maps SNI → upstream name)."""

    sni: str  # e.g. "www.microsoft.com", '""', "default"
    upstream: str  # upstream name


@dataclass
class NginxStreamInclude:
    """An include directive inside the stream map block."""

    path: str  # e.g. "/etc/nginx/stream.d/relay-maps/*.conf"


@dataclass
class NginxStreamBlock:
    """Complete nginx stream {} block for SNI-based routing.

    Renders: map_hash_bucket_size, SNI→backend map, upstream blocks,
    and the server block with ssl_preread.
    """

    listen_port: int = 443
    maps: list[NginxStreamMap] = field(default_factory=list)
    includes: list[NginxStreamInclude] = field(default_factory=list)
    upstreams: list[NginxUpstream] = field(default_factory=list)
    default_upstream: str = ""
    map_hash_bucket_size: int = 128
    # Server block settings
    proxy_connect_timeout: str = "1s"
    proxy_timeout: str = "30m"
    proxy_socket_keepalive: bool = True
    # Header comments
    header_comments: list[str] = field(default_factory=list)
    flow_comments: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Render the complete stream block as an nginx config string.

        Output uses 8-space base indent for top-level directives and
        12-space indent for nested content (matching the original
        textwrap.dedent output from the f-string template).
        """
        lines: list[str] = []
        base = "        "  # 8 spaces — top-level inside stream {}
        inner = "            "  # 12 spaces — inside blocks

        # Header comments
        for comment in self.header_comments:
            lines.append(f"{base}{comment}")

        # Flow comments — first line gets base indent (it's part of the
        # header block), subsequent lines have no indent (matches the
        # original textwrap.dedent + f-string interpolation behavior).
        if self.flow_comments:
            lines.append(f"{base}# Flow:")
            for i, fc in enumerate(self.flow_comments):
                if i == 0:
                    lines.append(f"{base}#   {fc}")
                else:
                    lines.append(f"#   {fc}")

        lines.append("")

        # map_hash_bucket_size
        lines.append(f"{base}map_hash_bucket_size {self.map_hash_bucket_size};")

        lines.append("")

        # SNI → backend map
        lines.append(f"{base}map $ssl_preread_server_name $meridian_backend {{")

        # Include directives inside the map
        for inc in self.includes:
            lines.append(f"{inner}include {inc.path};")

        # Map entries — use 4-space indent (matches original)
        for m in self.maps:
            lines.append(f"    {m.sni}  {m.upstream};")

        # Default entry
        if self.default_upstream:
            lines.append(f"    default  {self.default_upstream};")

        lines.append(f"{base}}}")

        lines.append("")

        # Upstream blocks
        for upstream in self.upstreams:
            lines.append(f"{base}upstream {upstream.name} {{")
            lines.append(f"{inner}server {upstream.server};")
            lines.append(f"{base}}}")
            lines.append("")

        # Server block
        lines.append(f"{base}server {{")
        lines.append(f"{inner}listen {self.listen_port};")
        lines.append(f"{inner}ssl_preread on;")
        lines.append(f"{inner}proxy_pass $meridian_backend;")

        # Proxy settings with comments
        lines.append(f"{inner}# Short timeout — don't wait 60s (default) if a backend is")
        lines.append(f"{inner}# temporarily unavailable.")
        lines.append(f"{inner}proxy_connect_timeout {self.proxy_connect_timeout};")

        lines.append(f"{inner}# VPN sessions can idle for extended periods (user not browsing).")
        lines.append(f"{inner}# Default 10m kills these; 30m is more forgiving while still")
        lines.append(f"{inner}# reclaiming truly dead connections.")
        lines.append(f"{inner}proxy_timeout {self.proxy_timeout};")

        lines.append(f"{inner}# TCP keepalives prevent NATs/firewalls from dropping idle")
        lines.append(f"{inner}# connections — critical for relay→exit paths.")
        if self.proxy_socket_keepalive:
            lines.append(f"{inner}proxy_socket_keepalive on;")

        lines.append(f"{base}}}")
        lines.append("")  # trailing newline

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP layer (TLS termination + reverse proxy + web serving)
# ---------------------------------------------------------------------------


@dataclass
class NginxLocationBlock:
    """A single location {} block inside an http server."""

    path: str  # e.g. "/ws-path", "= /xh-abc"
    proxy_pass: str = ""
    alias: str = ""
    return_code: str = ""  # e.g. "403", "404", "301 https://..."
    headers: dict[str, str] = field(default_factory=dict)
    proxy_headers: dict[str, str] = field(default_factory=dict)
    websocket: bool = False
    proxy_http_version: str = ""
    extra_directives: list[str] = field(default_factory=list)


def build_stream_block(
    reality_sni: str,
    reality_backend_port: int,
    nginx_internal_port: int,
    server_ip: str = "",
    domain: str = "",
) -> NginxStreamBlock:
    """Build the SNI routing stream block from deploy parameters.

    This is the model-based replacement for the parameters that
    ``_render_nginx_stream_config`` used to accept directly.
    """
    # --- SNI map entries ---
    maps: list[NginxStreamMap] = [
        NginxStreamMap(sni=reality_sni, upstream="xray_reality"),
    ]
    if server_ip:
        maps.append(NginxStreamMap(sni=server_ip, upstream="nginx_https"))
    if domain:
        maps.append(NginxStreamMap(sni=domain, upstream="nginx_https"))
    # No SNI (browsers connecting to bare IP per RFC 6066)
    maps.append(NginxStreamMap(sni='""', upstream="nginx_https"))

    # --- Flow comments ---
    flow: list[str] = [
        f"SNI={reality_sni} -> Xray Reality (127.0.0.1:{reality_backend_port})",
    ]
    if server_ip:
        flow.append(f"SNI={server_ip} -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    if domain:
        flow.append(f"SNI={domain} -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    flow.append(f"No SNI (bare IP) -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    flow.append(f"Unknown SNI -> TCP proxy to {reality_sni}:443 (no differential)")

    return NginxStreamBlock(
        listen_port=443,
        header_comments=[
            "# nginx SNI Router (stream module)",
            "# Managed by Meridian. Manual edits will be overwritten on next deploy.",
            "#",
        ],
        flow_comments=flow,
        includes=[
            NginxStreamInclude(path="/etc/nginx/stream.d/relay-maps/*.conf"),
        ],
        maps=maps,
        default_upstream="reality_dest",
        upstreams=[
            NginxUpstream(
                name="xray_reality",
                server=f"127.0.0.1:{reality_backend_port}",
            ),
            NginxUpstream(
                name="nginx_https",
                server=f"127.0.0.1:{nginx_internal_port}",
            ),
            NginxUpstream(
                name="reality_dest",
                server=f"{reality_sni}:443",
            ),
        ],
    )


@dataclass
class NginxHttpServerBlock:
    """An http server {} block.

    Handles TLS termination, reverse proxying, and static file serving.
    """

    listen_port: int
    server_name: str = "_"
    listen_address: str = ""  # e.g. "127.0.0.1" — empty = all interfaces
    ssl: bool = True
    http2: bool = True
    ssl_certificate: str = ""
    ssl_certificate_key: str = ""
    ssl_protocols: str = ""
    server_tokens: bool = False
    locations: list[NginxLocationBlock] = field(default_factory=list)
    access_log: str = ""
    extra_directives: list[str] = field(default_factory=list)
