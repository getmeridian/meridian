"""Pure nginx config rendering functions.

These functions build nginx configuration strings from parameters.
No SSH, no conn.run(), no side effects — pure string construction.
Extracted from nginx.py to keep that file under the 800-line budget.
"""

from __future__ import annotations

import textwrap

# ---------------------------------------------------------------------------
# nginx stream configuration (SNI routing — replaces HAProxy)
# ---------------------------------------------------------------------------


def render_nginx_stream_config(
    reality_sni: str,
    reality_backend_port: int,
    nginx_internal_port: int,
    server_ip: str = "",
    domain: str = "",
) -> str:
    """Render the nginx stream configuration for SNI-based routing.

    nginx stream sits on port 443 and inspects the TLS ClientHello SNI
    WITHOUT terminating TLS. Reality-targeted SNIs go to Xray, server
    IP/domain/no-SNI go to nginx HTTPS (connection pages).
    Unknown SNIs are TCP-proxied to the Reality dest site — a censor
    probing with SNI=google.com sees the dest site's real cert, not
    nginx's, eliminating the SNI routing differential.
    """
    # Build SNI → backend map entries
    map_entries = [
        # Per-relay SNI entries are included from individual files.
        # Each relay gets its own map file created during relay deploy.
        "    include /etc/nginx/stream.d/relay-maps/*.conf;",
        f"    {reality_sni}  xray_reality;",
    ]
    if server_ip:
        map_entries.append(f"    {server_ip}  nginx_https;")
    if domain:
        map_entries.append(f"    {domain}  nginx_https;")

    # No SNI (browsers connecting to bare IP per RFC 6066) → nginx
    # (needed for connection pages accessed via https://<IP>/...)
    map_entries.append('    ""  nginx_https;')
    # Unknown SNI → proxy to Reality dest (eliminates SNI differential —
    # censor probing with random SNIs sees the dest site, not nginx)
    map_entries.append("    default  reality_dest;")

    map_block = "\n".join(map_entries)

    # Flow comment lines
    flow_lines = [
        f"SNI={reality_sni} -> Xray Reality (127.0.0.1:{reality_backend_port})",
    ]
    if server_ip:
        flow_lines.append(f"SNI={server_ip} -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    if domain:
        flow_lines.append(f"SNI={domain} -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    flow_lines.append(f"No SNI (bare IP) -> nginx HTTPS (127.0.0.1:{nginx_internal_port})")
    flow_lines.append(f"Unknown SNI -> TCP proxy to {reality_sni}:443 (no differential)")
    flow_comment = "\n".join(f"#   {line}" for line in flow_lines)

    return textwrap.dedent(f"""\
        # nginx SNI Router (stream module)
        # Managed by Meridian. Manual edits will be overwritten on next deploy.
        #
        # Flow:
        {flow_comment}

        map_hash_bucket_size 128;

        map $ssl_preread_server_name $meridian_backend {{
        {map_block}
        }}

        upstream xray_reality {{
            server 127.0.0.1:{reality_backend_port};
        }}

        upstream nginx_https {{
            server 127.0.0.1:{nginx_internal_port};
        }}

        upstream reality_dest {{
            server {reality_sni}:443;
        }}

        server {{
            listen 443;
            listen [::]:443;
            ssl_preread on;
            proxy_pass $meridian_backend;
            # Short timeout — don't wait 60s (default) if a backend is
            # temporarily unavailable.
            proxy_connect_timeout 1s;
            # VPN sessions can idle for extended periods (user not browsing).
            # Default 10m kills these; 30m is more forgiving while still
            # reclaiming truly dead connections.
            proxy_timeout 30m;
            # TCP keepalives prevent NATs/firewalls from dropping idle
            # connections — critical for relay→exit paths.
            proxy_socket_keepalive on;
        }}
    """)


# ---------------------------------------------------------------------------
# nginx http configuration (TLS + reverse proxy + web — replaces Caddy)
# ---------------------------------------------------------------------------


def render_xhttp_location(xhttp_path: str) -> str:
    """Render the XHTTP reverse proxy location block."""
    return textwrap.dedent(f"""\

        # --- VLESS+XHTTP (enhanced stealth, nginx-terminated TLS) ---
        # Xray expects the canonical path without a trailing slash, but some
        # clients/browsers probe both forms. Route both to the same upstream.
        location = /{xhttp_path} {{
            proxy_pass http://meridian_xhttp;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
            proxy_buffering off;
            proxy_request_buffering off;
        }}

        # Long timeouts: XHTTP mode=auto lets clients negotiate streaming
        # modes (stream-one/stream-up) with long-lived connections.
        location /{xhttp_path}/ {{
            proxy_pass http://meridian_xhttp;
            proxy_http_version 1.1;
            # Empty Connection header enables upstream keepalive reuse —
            # without this, nginx sends Connection: close per request.
            proxy_set_header Connection "";
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
            proxy_buffering off;
            proxy_request_buffering off;
        }}
    """).rstrip()


def render_xhttp_upstream(xhttp_internal_port: int) -> str:
    """Render the XHTTP upstream keepalive pool block."""
    return textwrap.dedent(f"""\
        upstream meridian_xhttp {{
            server 127.0.0.1:{xhttp_internal_port};
            keepalive 32;
            keepalive_requests 10000;
            keepalive_timeout 300s;
        }}
    """)


def render_nginx_http_config(
    domain: str,
    nginx_internal_port: int,
    ws_path: str,
    wss_internal_port: int,
    panel_web_base_path: str,
    panel_internal_port: int,
    info_page_path: str,
    xhttp_path: str = "",
    xhttp_internal_port: int = 0,
    subscription_page_path: str = "",
    subscription_page_port: int = 0,
) -> str:
    """Render the nginx http configuration for domain mode.

    Architecture: nginx stream (port 443) -> nginx http (internal port)
    nginx stream does SNI routing without TLS termination.
    nginx http handles TLS with certificates issued by acme.sh.
    """
    wss_block = textwrap.dedent(f"""\

        # --- VLESS+WSS Fallback (Cloudflare CDN path) ---
        location /{ws_path} {{
            proxy_pass http://127.0.0.1:{wss_internal_port};
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_read_timeout 360s;
        }}
    """).rstrip()

    xhttp_block = ""
    xhttp_upstream = ""
    if xhttp_path and xhttp_internal_port > 0:
        xhttp_block = render_xhttp_location(xhttp_path)
        xhttp_upstream = render_xhttp_upstream(xhttp_internal_port)

    # Root: nginx's built-in 403 page. NOT a custom Meridian page — custom
    # HTML would be fingerprintable (one known server reveals all others).
    # nginx generates 403/404 bodies itself, identical across all installs.
    root_action = "return 403;"
    default_action = "return 404;"

    return render_nginx_server_block(
        host=domain,
        nginx_internal_port=nginx_internal_port,
        panel_web_base_path=panel_web_base_path,
        panel_internal_port=panel_internal_port,
        info_page_path=info_page_path,
        extra_locations=wss_block + xhttp_block,
        upstream_blocks=xhttp_upstream,
        root_action=root_action,
        default_action=default_action,
        mode_comment="Domain Mode",
        tls_comment=(f"TLS: certificates issued by acme.sh for {domain}"),
        redirect_http=True,
        subscription_page_path=subscription_page_path,
        subscription_page_port=subscription_page_port,
    )


def render_nginx_ip_config(
    server_ip: str,
    nginx_internal_port: int,
    panel_web_base_path: str,
    panel_internal_port: int,
    info_page_path: str,
    xhttp_path: str = "",
    xhttp_internal_port: int = 0,
    subscription_page_path: str = "",
    subscription_page_port: int = 0,
) -> str:
    """Render nginx http configuration for IP certificate mode (no domain).

    Architecture: nginx stream (port 443) -> nginx http (internal port)
    TLS via Let's Encrypt IP certificate (acme.sh --certificate-profile shortlived).
    """
    xhttp_block = ""
    xhttp_upstream = ""
    if xhttp_path and xhttp_internal_port > 0:
        xhttp_block = render_xhttp_location(xhttp_path)
        xhttp_upstream = render_xhttp_upstream(xhttp_internal_port)

    # Root: nginx's built-in 403 — see domain mode comment for rationale.
    root_action = "return 403;"
    default_action = "return 404;"

    return render_nginx_server_block(
        host=server_ip,
        nginx_internal_port=nginx_internal_port,
        panel_web_base_path=panel_web_base_path,
        panel_internal_port=panel_internal_port,
        info_page_path=info_page_path,
        extra_locations=xhttp_block,
        upstream_blocks=xhttp_upstream,
        root_action=root_action,
        default_action=default_action,
        mode_comment="IP Certificate Mode",
        tls_comment=("TLS: Let's Encrypt IP certificate (acme.sh, shortlived profile)"),
        redirect_http=False,
        subscription_page_path=subscription_page_path,
        subscription_page_port=subscription_page_port,
    )


def render_nginx_server_block(
    host: str,
    nginx_internal_port: int,
    panel_web_base_path: str,
    panel_internal_port: int,
    info_page_path: str,
    extra_locations: str,
    root_action: str,
    default_action: str,
    mode_comment: str,
    tls_comment: str,
    redirect_http: bool = True,
    upstream_blocks: str = "",
    subscription_page_path: str = "",
    subscription_page_port: int = 0,
) -> str:
    """Render the shared nginx server block structure.

    Used by both domain and IP config renderers to avoid duplication.
    redirect_http: True = HTTP→HTTPS redirect (domain mode, has real content).
                   False = ACME-only, no redirect (IP mode — redirect to
                   HTTPS that returns 403 is a contradiction signal).
    """
    csp = "default-src 'self'; img-src 'self' data:; connect-src 'self'"

    # Port 80 behavior: domain mode redirects (has real content),
    # IP mode serves ACME challenges only (no redirect — redirect to
    # HTTPS that returns 403 is a contradiction signal for censors).
    if redirect_http:
        http_default = "return 301 https://$host$request_uri;"
    else:
        http_default = "return 403;"

    # Subscription page proxy (Remnawave subscription frontend)
    subscription_block = ""
    if subscription_page_path and subscription_page_port > 0:
        subscription_block = textwrap.dedent(f"""\

            # --- Subscription Page (Remnawave subscription frontend) ---
            location /{subscription_page_path}/ {{
                proxy_pass http://127.0.0.1:{subscription_page_port}/;
                proxy_http_version 1.1;
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
            }}
        """).rstrip()

    return textwrap.dedent(f"""\
        # Meridian Proxy Configuration ({mode_comment})
        # Managed by Meridian — this file is overwritten on each deploy.
        #
        # Architecture: nginx stream (port 443) -> nginx http (port {nginx_internal_port})
        # {tls_comment}

        # --- Cache control for connection pages (map avoids add_header inheritance) ---
        map $uri $meridian_cache {{
            ~*/pwa/            "public, max-age=86400";
            ~*/config\\.json$   "no-cache, must-revalidate";
            ~*/sub\\.txt$       "no-cache, must-revalidate";
            ~*/stats/          "no-cache, must-revalidate";
            default            "no-store";
        }}

        map $uri $meridian_sw {{
            ~*/sw\\.js$   "/";
            default      "";
        }}

        # WebSocket upgrade: only set Connection: upgrade when client sends Upgrade header
        map $http_upgrade $connection_upgrade {{
            default upgrade;
            ""      close;
        }}
    {upstream_blocks}
        server {{
            listen 127.0.0.1:{nginx_internal_port} ssl http2;
            server_name {host};
            server_tokens off;

            ssl_certificate     /etc/ssl/meridian/fullchain.pem;
            ssl_certificate_key /etc/ssl/meridian/key.pem;
            ssl_protocols TLSv1.2 TLSv1.3;
    {extra_locations}

            # --- Remnawave Panel (management interface on secret path) ---
            location /{panel_web_base_path}/ {{
                proxy_pass http://127.0.0.1:{panel_internal_port}/;
                proxy_http_version 1.1;
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_set_header Upgrade $http_upgrade;
                proxy_set_header Connection $connection_upgrade;

                # Rewrite paths for subpath deployment.  Remnawave is not
                # designed for sub-paths (docs: "must be hosted on root path"),
                # so we patch responses with sub_filter.
                #
                # HTML: href/src for assets, favicons, splash_screens
                #       CSS url() for fonts
                # JS:   axios baseURL, i18next loadPath, React Router
                #       routes (/dashboard, /auth, /oauth2), Lottie srcs
                #
                # This eliminates all root-level paths — every probe
                # returns stock nginx 403/404.
                proxy_set_header Accept-Encoding "";
                sub_filter_once off;
                sub_filter_types application/javascript text/javascript;
                sub_filter 'href="/a' 'href="/{panel_web_base_path}/a';
                sub_filter 'href="/f' 'href="/{panel_web_base_path}/f';
                sub_filter 'href="/s' 'href="/{panel_web_base_path}/s';
                sub_filter 'src="/a' 'src="/{panel_web_base_path}/a';
                sub_filter 'url(/a' 'url(/{panel_web_base_path}/a';
                sub_filter '=window.location.origin;' '=window.location.origin+"/{panel_web_base_path}";';
                sub_filter '"/locales/' '"/{panel_web_base_path}/locales/';
                sub_filter '"/api/queues' '"/{panel_web_base_path}/api/queues';
                sub_filter '"/dashboard' '"/{panel_web_base_path}/dashboard';
                sub_filter '"/auth' '"/{panel_web_base_path}/auth';
                sub_filter '"/oauth2/' '"/{panel_web_base_path}/oauth2/';
                sub_filter '"/lotties/' '"/{panel_web_base_path}/lotties/';
            }}

    {subscription_block}

            # --- Connection Info Pages (PWA with per-client config) ---
            # alias strips the location prefix (like Caddy's handle_path).
            location /{info_page_path}/ {{
                alias /var/www/private/;

                add_header Cache-Control $meridian_cache always;
                add_header Service-Worker-Allowed $meridian_sw always;
                add_header Content-Security-Policy "{csp}" always;
                add_header X-Content-Type-Options "nosniff" always;
                add_header X-Frame-Options "DENY" always;
                add_header Referrer-Policy "no-referrer" always;
            }}

            # Root: nginx-generated 403 (not custom HTML — avoids fingerprinting)
            location = / {{
                {root_action}
            }}

            # Default: stock nginx 404 — indistinguishable from any nginx server.
            # No root-level proxies — all panel traffic goes through the secret
            # path location above (sub_filter rewrites JS to use it).
            location / {{
                {default_action}
            }}

            access_log /var/log/nginx/meridian.log;
        }}

        # --- HTTP: ACME challenge{" + redirect" if redirect_http else " only (no redirect)"} ---
        server {{
            listen 80;
            listen [::]:80;
            server_name {host};
            server_tokens off;

            location /.well-known/acme-challenge/ {{
                root /var/www/acme;
            }}

            location / {{
                {http_default}
            }}
        }}
    """)
