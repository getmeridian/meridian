"""nginx provisioning steps: stream SNI routing, HTTP config, install and configure.

nginx handles both SNI-based TCP routing (stream module) and TLS termination
+ web serving (http module), replacing the previous HAProxy + Caddy setup.
"""

from __future__ import annotations

import re
import shlex
import textwrap

from meridian.config import REMNAWAVE_PANEL_PORT, REMNAWAVE_SUBSCRIPTION_PAGE_PORT
from meridian.provision.ensure import resolve_ctx
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

# ---------------------------------------------------------------------------
# nginx stream configuration (SNI routing — replaces HAProxy)
# ---------------------------------------------------------------------------


def _render_nginx_stream_config(
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


def _render_xhttp_location(xhttp_path: str) -> str:
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


def _render_xhttp_upstream(xhttp_internal_port: int) -> str:
    """Render the XHTTP upstream keepalive pool block."""
    return textwrap.dedent(f"""\
        upstream meridian_xhttp {{
            server 127.0.0.1:{xhttp_internal_port};
            keepalive 32;
            keepalive_requests 10000;
            keepalive_timeout 300s;
        }}
    """)


def _render_nginx_http_config(
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
        xhttp_block = _render_xhttp_location(xhttp_path)
        xhttp_upstream = _render_xhttp_upstream(xhttp_internal_port)

    # Root: nginx's built-in 403 page. NOT a custom Meridian page — custom
    # HTML would be fingerprintable (one known server reveals all others).
    # nginx generates 403/404 bodies itself, identical across all installs.
    root_action = "return 403;"
    default_action = "return 404;"

    return _render_nginx_server_block(
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


def _render_nginx_ip_config(
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
        xhttp_block = _render_xhttp_location(xhttp_path)
        xhttp_upstream = _render_xhttp_upstream(xhttp_internal_port)

    # Root: nginx's built-in 403 — see domain mode comment for rationale.
    root_action = "return 403;"
    default_action = "return 404;"

    return _render_nginx_server_block(
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


def _render_nginx_server_block(
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


# ---------------------------------------------------------------------------
# InstallNginx — install nginx binary, stream module, and acme.sh
# ---------------------------------------------------------------------------


class InstallNginx:
    """Install nginx, stream module, and acme.sh.

    Handles upgrade path from old HAProxy+Caddy stack, version
    requirements (>=1.16), and the nginx.org official repo fallback.
    """

    name = "Install nginx"

    def __init__(self, email: str = "") -> None:
        self.email = email

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        changed = False

        # -- Upgrade path: stop old HAProxy and Caddy if present --
        conn.run(
            "systemctl stop haproxy 2>/dev/null; systemctl disable haproxy 2>/dev/null; true",
            timeout=15,
        )
        conn.run(
            "systemctl stop caddy 2>/dev/null; systemctl disable caddy 2>/dev/null; true",
            timeout=15,
        )
        # Remove old watchdog immediately to prevent it from restarting
        # haproxy/caddy during the deploy (cron runs every 5 min)
        conn.run("rm -f /etc/meridian/health-check.sh", timeout=15)
        # Clean up old config files and cert storage
        conn.run(
            "rm -f /etc/haproxy/haproxy.cfg /etc/caddy/conf.d/meridian.caddy /etc/caddy/Caddyfile && "
            "rm -rf /etc/systemd/system/haproxy.service.d /etc/systemd/system/caddy.service.d "
            "/var/lib/caddy/.local/share/caddy && "
            "systemctl daemon-reload 2>/dev/null; true",
            timeout=15,
        )

        # -- Check if nginx is already installed and meets version requirement --
        check = conn.run("dpkg -l nginx 2>/dev/null | grep -q '^ii'", timeout=15)
        already_installed = check.returncode == 0
        needs_official_repo = False

        if already_installed:
            ver_check = conn.run("nginx -v 2>&1", timeout=15)
            ver_output = ver_check.stdout + ver_check.stderr
            m = re.search(r"nginx/(\d+)\.(\d+)", ver_output)
            if m and (int(m.group(1)), int(m.group(2))) < (1, 16):
                needs_official_repo = True
        else:
            # Not installed — try distro repo first, upgrade if too old
            result = conn.run(
                "DEBIAN_FRONTEND=noninteractive apt-get install -y nginx",
                timeout=180,
            )
            if result.returncode != 0:
                # Distro install failed — fall through to official repo
                needs_official_repo = True
            else:
                changed = True
                ver_check = conn.run("nginx -v 2>&1", timeout=15)
                ver_output = ver_check.stdout + ver_check.stderr
                m = re.search(r"nginx/(\d+)\.(\d+)", ver_output)
                if m and (int(m.group(1)), int(m.group(2))) < (1, 16):
                    needs_official_repo = True

        if needs_official_repo:
            # Install from official nginx.org repo (mirrors Docker pattern)
            distro = conn.run("bash -c '. /etc/os-release && echo $ID'", timeout=15)
            distro_name = distro.stdout.strip().lower() if distro.returncode == 0 else "ubuntu"

            codename = conn.run("bash -c '. /etc/os-release && echo $VERSION_CODENAME'", timeout=15)
            distro_codename = codename.stdout.strip() if codename.returncode == 0 else "jammy"

            # Remove conflicting distro packages before official repo install
            conn.run(
                "DEBIAN_FRONTEND=noninteractive apt-get remove -y"
                " nginx-common nginx-core nginx-full 'libnginx-mod-*' 2>/dev/null; true",
                timeout=120,
            )

            # Ensure keyrings directory exists (missing on Ubuntu < 22.04)
            conn.run("mkdir -p /etc/apt/keyrings && chmod 755 /etc/apt/keyrings", timeout=15)

            # Add nginx.org signing key
            result = conn.run(
                "curl -fsSL https://nginx.org/keys/nginx_signing.key"
                " -o /etc/apt/keyrings/nginx.asc"
                " && chmod 644 /etc/apt/keyrings/nginx.asc",
                timeout=60,
            )
            if result.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to add nginx signing key: {result.stderr.strip()[:200]}",
                )

            # Add nginx.org stable repo
            repo_line = (
                f"deb [signed-by=/etc/apt/keyrings/nginx.asc] "
                f"https://nginx.org/packages/{distro_name} "
                f"{distro_codename} nginx"
            )
            conn.put_text(
                "/etc/apt/sources.list.d/nginx-official.list",
                repo_line + "\n",
                mode="644",
                timeout=15,
            )

            # Pin official nginx packages higher to override distro
            conn.put_text(
                "/etc/apt/preferences.d/99nginx",
                "Package: nginx*\nPin: origin nginx.org\nPin-Priority: 900\n",
                mode="644",
                timeout=15,
            )

            result = conn.run(
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq"
                " && DEBIAN_FRONTEND=noninteractive apt-get install -y nginx",
                timeout=180,
            )
            if result.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to install nginx from official repo: {result.stderr.strip()[:200]}",
                )

            changed = True

            # Clean up stale load_module directives — official nginx has
            # stream compiled statically, old distro nginx.conf may reference
            # dynamic .so files that no longer exist.
            conn.run(
                "sed -i '/load_module.*ngx_stream_module/d' /etc/nginx/nginx.conf 2>/dev/null; true",
                timeout=15,
            )

        # -- Ensure stream module is available --
        conn.run(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libnginx-mod-stream 2>/dev/null; true",
            timeout=120,
        )
        check = conn.run(
            "test -f /usr/lib/nginx/modules/ngx_stream_module.so || nginx -V 2>&1 | grep -q 'with-stream '",
            timeout=15,
        )
        if check.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail="nginx stream module not available — install libnginx-mod-stream",
            )

        # -- Create directories --
        conn.run(
            "mkdir -p /var/www/private /var/www/acme/.well-known/acme-challenge "
            "/etc/ssl/meridian /etc/nginx/stream.d /etc/nginx/stream.d/relay-maps && "
            "chmod 700 /etc/ssl/meridian && "
            "chown -R www-data:www-data /var/www/private /var/www/acme",
            timeout=15,
        )

        # -- Ensure webmanifest MIME type is registered --
        conn.run(
            "grep -q webmanifest /etc/nginx/mime.types || "
            r"sed -i '/^}/i \    application/manifest+json  webmanifest;' /etc/nginx/mime.types",
            timeout=15,
        )

        # -- Install acme.sh (if not already installed) --
        check = conn.run("test -f /root/.acme.sh/acme.sh", timeout=15)
        if check.returncode != 0:
            # email='' breaks acme.sh installer (shift error), omit when empty
            email_flag = f"email={shlex.quote(self.email)}" if self.email else ""
            result = conn.run(
                f"curl -fsSL https://get.acme.sh | sh -s -- {email_flag}",
                timeout=120,
            )
            if result.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to install acme.sh: {result.stderr.strip()}",
                )
            changed = True

        cron_check = conn.run("crontab -l 2>/dev/null | grep -q 'acme.sh --cron'", timeout=15)
        result = conn.run("/root/.acme.sh/acme.sh --install-cronjob", timeout=60)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"Failed to install acme.sh cron job: {detail[:200]}",
            )
        if cron_check.returncode != 0:
            changed = True

        return StepResult(name=self.name, status="changed" if changed else "ok")


# ---------------------------------------------------------------------------
# ConfigureNginx — deploy configs, validate, start/reload
# ---------------------------------------------------------------------------


class ConfigureNginx:
    """Deploy nginx stream + http configs, validate, and start/reload.

    Reads context values set by ConfigurePanel for paths and ports.
    """

    name = "Configure nginx"

    def __init__(
        self,
        domain: str,
        reality_sni: str | None = None,
        reality_backend_port: int | None = None,
        nginx_internal_port: int = 8443,
        ws_path: str | None = None,
        wss_internal_port: int | None = None,
        panel_web_base_path: str | None = None,
        panel_internal_port: int | None = None,
        info_page_path: str | None = None,
        server_ip: str | None = None,
        skip_dns_check: bool = False,
        ip_mode: bool = False,
        xhttp_path: str | None = None,
        xhttp_internal_port: int | None = None,
        subscription_page_path: str | None = None,
        subscription_page_port: int | None = None,
    ) -> None:
        self.domain = domain
        self.reality_sni = reality_sni
        self.reality_backend_port = reality_backend_port
        self.nginx_internal_port = nginx_internal_port
        self.ws_path = ws_path
        self.wss_internal_port = wss_internal_port
        self.panel_web_base_path = panel_web_base_path
        self.panel_internal_port = panel_internal_port
        self.info_page_path = info_page_path
        self.server_ip = server_ip
        self.skip_dns_check = skip_dns_check
        self.ip_mode = ip_mode
        self.xhttp_path = xhttp_path
        self.xhttp_internal_port = xhttp_internal_port
        self.subscription_page_path = subscription_page_path
        self.subscription_page_port = subscription_page_port

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        # Resolve runtime values from context (populated by ConfigurePanel).
        panel_web_base_path = resolve_ctx(self.panel_web_base_path, ctx.get("web_base_path", ""))
        info_page_path = resolve_ctx(self.info_page_path, ctx.get("info_page_path", ""))
        panel_internal_port = resolve_ctx(self.panel_internal_port, REMNAWAVE_PANEL_PORT)
        server_ip = resolve_ctx(self.server_ip, ctx.ip)
        xhttp_path = resolve_ctx(self.xhttp_path, ctx.get("xhttp_path", ""))
        xhttp_internal_port = resolve_ctx(
            self.xhttp_internal_port,
            ctx.xhttp_port if ctx.xhttp_enabled else 0,
        )
        ws_path = resolve_ctx(self.ws_path, ctx.get("ws_path", ""))
        wss_internal_port = resolve_ctx(self.wss_internal_port, ctx.wss_port)
        reality_sni = resolve_ctx(self.reality_sni, ctx.sni)
        reality_backend_port = resolve_ctx(self.reality_backend_port, ctx.reality_port)
        sub_page_path = resolve_ctx(self.subscription_page_path, ctx.get("subscription_page_path", ""))
        sub_page_port = resolve_ctx(self.subscription_page_port, REMNAWAVE_SUBSCRIPTION_PAGE_PORT)

        # -- DNS pre-check (domain mode only) --
        if not self.ip_mode and not self.skip_dns_check:
            dns_result = _check_domain_dns(conn, self.domain, server_ip)
            if dns_result is not None:
                return StepResult(name=self.name, status="failed", detail=dns_result)

        # -- Bootstrap: generate self-signed cert so nginx can start --
        check = conn.run("test -f /etc/ssl/meridian/fullchain.pem", timeout=15)
        if check.returncode != 0:
            cert_host = server_ip if self.ip_mode else self.domain
            q_subj = shlex.quote(f"/CN={cert_host}")
            san_ext = shlex.quote(f"subjectAltName=DNS:{cert_host}")
            if self.ip_mode:
                san_ext = shlex.quote(f"subjectAltName=IP:{cert_host}")
            result = conn.run(
                f"openssl req -x509 -newkey rsa:2048 -keyout /etc/ssl/meridian/key.pem "
                f"-out /etc/ssl/meridian/fullchain.pem -days 1 -nodes "
                f"-subj {q_subj} -addext {san_ext} && "
                f"chmod 600 /etc/ssl/meridian/key.pem",
                timeout=15,
            )
            if result.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail="Failed to generate bootstrap certificate",
                )

        # -- Deploy nginx stream config --
        stream_config = _render_nginx_stream_config(
            reality_sni=reality_sni,
            reality_backend_port=reality_backend_port,
            nginx_internal_port=self.nginx_internal_port,
            server_ip=server_ip,
            domain=self.domain,
        )
        result = conn.put_text(
            "/etc/nginx/stream.d/meridian.conf",
            stream_config,
            mode="644",
            timeout=15,
            operation_name="write nginx stream config",
        )
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"Failed to write stream config: {result.stderr.strip()}",
            )

        # -- Deploy nginx http config --
        if self.ip_mode:
            http_config = _render_nginx_ip_config(
                server_ip=server_ip,
                nginx_internal_port=self.nginx_internal_port,
                panel_web_base_path=panel_web_base_path,
                panel_internal_port=panel_internal_port,
                info_page_path=info_page_path,
                xhttp_path=xhttp_path,
                xhttp_internal_port=xhttp_internal_port,
                subscription_page_path=sub_page_path,
                subscription_page_port=sub_page_port,
            )
        else:
            http_config = _render_nginx_http_config(
                domain=self.domain,
                nginx_internal_port=self.nginx_internal_port,
                ws_path=ws_path,
                wss_internal_port=wss_internal_port,
                panel_web_base_path=panel_web_base_path,
                panel_internal_port=panel_internal_port,
                info_page_path=info_page_path,
                xhttp_path=xhttp_path,
                xhttp_internal_port=xhttp_internal_port,
                subscription_page_path=sub_page_path,
                subscription_page_port=sub_page_port,
            )
        result = conn.put_text(
            "/etc/nginx/conf.d/meridian-http.conf",
            http_config,
            mode="644",
            timeout=15,
            operation_name="write nginx http config",
        )
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"Failed to write http config: {result.stderr.strip()}",
            )

        # -- Ensure nginx.conf has a stream block --
        check = conn.run("grep -q 'stream {' /etc/nginx/nginx.conf", timeout=15)
        if check.returncode != 0:
            current = conn.get_text("/etc/nginx/nginx.conf", timeout=15)
            if current.returncode == 0:
                stream_block = "\nstream {\n    include /etc/nginx/stream.d/*.conf;\n}\n"
                conn.put_text(
                    "/etc/nginx/nginx.conf",
                    current.stdout.rstrip() + stream_block,
                    mode="644",
                    timeout=15,
                    operation_name="append nginx stream block",
                )

        # -- Remove default site (conflicts with our port 80 listener) --
        conn.run("rm -f /etc/nginx/sites-enabled/default", timeout=15)

        # -- Validate configuration --
        result = conn.run("nginx -t 2>&1", timeout=15)
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"nginx config validation failed: {result.stderr.strip() or result.stdout.strip()}",
            )

        # -- Ensure nginx restarts on failure --
        result = conn.put_text(
            "/etc/systemd/system/nginx.service.d/restart.conf",
            "[Service]\nRestart=on-failure\nRestartSec=5\n",
            mode="644",
            create_parent=True,
            timeout=15,
            operation_name="write nginx restart override",
        )
        if result.returncode == 0:
            conn.run("systemctl daemon-reload", timeout=15)

        # -- Start/enable/reload nginx --
        conn.run("systemctl enable nginx", timeout=15)
        result = conn.run("systemctl reload-or-restart nginx", timeout=30)
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"Failed to start nginx: {result.stderr.strip()}",
            )

        host = server_ip if self.ip_mode else self.domain
        return StepResult(
            name=self.name,
            status="changed",
            detail=f"nginx configured for {host}:{self.nginx_internal_port}",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_domain_dns(conn: ServerConnection, domain: str, server_ip: str) -> str | None:
    """Check if the domain resolves to the server IP.

    Returns an error message if DNS check fails, None if OK.
    """
    q_domain = shlex.quote(domain)
    result = conn.run(f"dig +short {q_domain}", timeout=15)
    resolved = result.stdout.strip() if result.returncode == 0 else ""

    if not resolved:
        # Empty DNS response -- might be a new domain, let it pass
        return None

    if resolved != server_ip:
        return (
            f"{domain} does not resolve to this server's IP ({server_ip}).\n"
            f"DNS returned: {resolved}\n\n"
            f"The domain must point DIRECTLY to this server for TLS certificates.\n\n"
            f"Fix: In Cloudflare, set the A record to 'DNS only' (grey cloud), then re-run.\n"
            f"After setup succeeds, switch to 'Proxied' (orange cloud).\n\n"
            f"To skip this check: use skip_dns_check=True"
        )

    return None


# ---------------------------------------------------------------------------
# DeployPWAAssets
# ---------------------------------------------------------------------------


class DeployPWAAssets:
    """Deploy shared PWA static assets to /var/www/private/pwa/.

    These assets (JS, CSS, service worker, icon) are identical for all
    clients and deployed once. Per-client connection pages (index.html,
    config.json, manifest, sub.txt) are generated by ``meridian client
    add`` / ``meridian client show`` via the panel API + ``render.py``.
    """

    name = "Deploy PWA assets"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        from meridian.pwa import upload_pwa_assets

        try:
            error = upload_pwa_assets(conn)
        except Exception as exc:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"Failed to load PWA assets: {exc}",
            )
        if error:
            return StepResult(
                name=self.name,
                status="failed",
                detail=error,
            )
        return StepResult(
            name=self.name,
            status="changed",
            detail="Shared PWA assets deployed to /var/www/private/pwa/",
        )
