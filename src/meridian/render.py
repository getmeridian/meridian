"""File output rendering — HTML and text connection summaries."""

from __future__ import annotations

import base64
import html as html_mod
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from meridian.models import ProtocolURL, RelayURLSet, derive_client_name
from meridian.urls import generate_qr_base64

if TYPE_CHECKING:
    import jinja2

logger = logging.getLogger(__name__)


def save_connection_html(
    protocol_urls: list[ProtocolURL],
    dest: Path,
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
) -> None:
    """Save a connection info HTML page with QR codes.

    Generates QR codes as base64 PNGs and embeds them inline.
    Uses the same HTML structure as the Jinja2 template when available,
    falling back to a minimal self-contained HTML page.

    Args:
        protocol_urls: Ordered list of active protocol URLs.
        dest: Destination file path.
        server_ip: Server IP address for display.
        domain: Optional domain name (enables WSS card in template).
        client_name: Client name for the page title (derived from URLs if
            omitted).
        relay_entries: Optional relay URL sets for relay-aware rendering.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Derive client name.
    name = client_name or derive_client_name(protocol_urls)

    # Attach QR codes to protocol URLs (ProtocolURL is frozen, so use replace).
    from dataclasses import replace

    urls_with_qr = [
        replace(p, qr_b64=generate_qr_base64(p.url)) if p.url and not p.qr_b64 else p for p in protocol_urls
    ]

    # Build template variables.
    variables = _build_template_variables(
        protocol_urls=urls_with_qr,
        server_ip=server_ip,
        domain=domain,
        now=now,
        is_server_hosted=False,
        client_name=name,
        relay_entries=relay_entries,
    )

    result_html = _render_template(
        template_text=_load_template_text(),
        variables=variables,
        protocol_urls=urls_with_qr,
        server_ip=server_ip,
        domain=domain,
        client_name=name,
        now=now,
    )

    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest.write_text(result_html)
    dest.chmod(0o600)


def render_hosted_html(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
) -> str:
    """Render connection info HTML for server hosting (is_server_hosted=True).

    Returns the rendered HTML string. Used by ``client add`` /
    ``client show`` for server-hosted pages.

    Each ``ProtocolURL`` should have its ``qr_b64`` field populated
    before calling this function.

    Args:
        protocol_urls: Ordered list of active protocol URLs with QR data.
        server_ip: Server IP address.
        domain: Optional domain (enables WSS card).
        client_name: Client name for page title.
        relay_entries: Optional relay URL sets for relay-aware rendering.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    variables = _build_template_variables(
        protocol_urls=protocol_urls,
        server_ip=server_ip,
        domain=domain,
        now=now,
        is_server_hosted=True,
        client_name=client_name,
        relay_entries=relay_entries,
    )

    return _render_template(
        template_text=_load_template_text(),
        variables=variables,
        protocol_urls=protocol_urls,
        server_ip=server_ip,
        domain=domain,
        client_name=client_name,
        now=now,
    )


# ---------------------------------------------------------------------------
# PWA rendering functions
# ---------------------------------------------------------------------------

# App download links — matches website/src/data/apps.json (single source of truth)
_PWA_APPS: list[dict[str, Any]] = [
    {
        "name": "ShadowRocket",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/shadowrocket/id932747118",
        "deeplink": "sub://{url_b64}",
    },
    {
        "name": "Streisand",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/streisand/id6450534064",
        "deeplink": "streisand://import/{url}#{name}",
    },
    {
        "name": "v2RayTun",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/v2raytun/id6476628951",
        "deeplink": "v2raytun://import/{url}",
    },
    {
        "name": "v2rayNG",
        "platform": "Android",
        "url": "https://github.com/2dust/v2rayNG/releases/latest",
        "deeplink": "v2rayng://install-sub?url={url}&name={name}",
    },
    {
        "name": "NekoBox",
        "platform": "Android",
        "url": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
        "deeplink": "sn://subscription?url={url}&name={name}",
    },
    {
        "name": "FlClash",
        "platform": "All platforms",
        "url": "https://github.com/chen08209/FlClash/releases/latest",
        "deeplink": "flclash://install-config?url={url}",
    },
    {
        "name": "sing-box",
        "platform": "All platforms",
        "url": "https://github.com/SagerNet/sing-box/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/sing-box-vt/id6673731168",
            "Android": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa",
        },
        "deeplink": "sing-box://import-remote-profile?url={url}#{name}",
    },
    {
        "name": "Hiddify",
        "platform": "All platforms",
        "url": "https://github.com/hiddify/hiddify-app/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
            "Android": "https://play.google.com/store/apps/details?id=app.hiddify.com",
        },
        "deeplink": "hiddify://install-config/?url={url}",
    },
    {
        "name": "Karing",
        "platform": "All platforms",
        "url": "https://github.com/KaringX/karing/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/karing/id6472431552",
            "Android": "https://play.google.com/store/apps/details?id=com.nebula.karing",
        },
        "deeplink": "karing://install-config?url={url}&name={name}",
    },
    {
        "name": "V2Box",
        "platform": "All platforms",
        "url": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
        "urls": {
            "iOS": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
            "Android": "https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box",
        },
        "deeplink": "v2box://install-sub?url={url_b64}&name={name}",
    },
    {
        "name": "Happ",
        "platform": "All platforms",
        "url": "https://happ.su/",
        "urls": {
            "iOS": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
            "Android": "https://play.google.com/store/apps/details?id=com.happproxy",
        },
        "deeplink": "happ://add/{url_raw}",
    },
    {"name": "v2rayN", "platform": "Windows", "url": "https://github.com/2dust/v2rayN/releases/latest"},
    {
        "name": "Clash Verge Rev",
        "platform": "Windows",
        "url": "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
        "deeplink": "clash://install-config?url={url}",
    },
]

# App icon name mapping: app name → icon filename (without extension).
# Icons stored as optimized WebP in src/meridian/icons/.
_APP_ICON_NAMES = {
    "ShadowRocket": "shadowrocket",
    "Streisand": "streisand",
    "v2RayTun": "v2raytun",
    "v2rayNG": "v2rayng",
    "NekoBox": "nekobox",
    "FlClash": "flclash",
    "sing-box": "sing-box",
    "Hiddify": "hiddify",
    "Karing": "karing",
    "V2Box": "v2box",
    "Happ": "happ",
    "v2rayN": "v2rayn",
    "Clash Verge Rev": "clash-verge-rev",
}

_app_icons_cache: dict[str, str] | None = None


def _load_app_icons() -> dict[str, str]:
    """Load app icons from package data and return {app_name: data_uri} dict."""
    global _app_icons_cache
    if _app_icons_cache is not None:
        return _app_icons_cache

    from importlib.resources import files

    icons_dir = files("meridian") / "icons"
    result: dict[str, str] = {}
    for app_name, filename in _APP_ICON_NAMES.items():
        resource = icons_dir / f"{filename}.webp"
        try:
            data = resource.read_bytes()
            b64 = base64.b64encode(data).decode()
            result[app_name] = f"data:image/webp;base64,{b64}"
        except (FileNotFoundError, OSError):
            pass
    _app_icons_cache = result
    return result


def render_config_json(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
    server_name: str = "",
    server_icon: str = "",
    color: str = "",
    subscription_url: str = "",
) -> str:
    """Render per-client config.json for the PWA shell.

    Returns a JSON string containing all connection data that the
    PWA's app.js needs to populate the page at runtime.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = client_name or derive_client_name(protocol_urls)

    protocols = []
    for i, p in enumerate(protocol_urls):
        if not p.url:
            continue
        protocols.append(
            {
                "key": p.key,
                "label": p.label,
                "url": p.url,
                "qr_b64": p.qr_b64,
                "recommended": i == 0,
            }
        )

    relays = []
    if relay_entries:
        for relay_set in relay_entries:
            relay_urls = []
            for purl in relay_set.urls:
                if purl.url:
                    relay_urls.append(
                        {
                            "key": purl.key,
                            "label": purl.label,
                            "url": purl.url,
                            "qr_b64": purl.qr_b64,
                        }
                    )
            if relay_urls:
                relays.append(
                    {
                        "ip": relay_set.relay_ip,
                        "name": relay_set.relay_name,
                        "urls": relay_urls,
                    }
                )

    icons = _load_app_icons()
    apps = [{**app, "icon": icons[app["name"]]} if app["name"] in icons else app for app in _PWA_APPS]

    config = {
        "version": 1,
        "client_name": name,
        "server_ip": server_ip,
        "domain": domain,
        "protocols": protocols,
        "relays": relays,
        "apps": apps,
        "generated_at": now,
    }
    if server_name:
        config["server_name"] = server_name
    if server_icon:
        config["server_icon"] = server_icon
    if color:
        config["color"] = color
    if subscription_url:
        from meridian.urls import generate_qr_base64

        config["subscription_url"] = subscription_url
        config["subscription_qr_b64"] = generate_qr_base64(subscription_url)
    return json.dumps(config, indent=2, ensure_ascii=False)


def render_subscription(
    protocol_urls: list[ProtocolURL],
    *,
    relay_entries: list[RelayURLSet] | None = None,
) -> str:
    """Render a V2Ray subscription file (base64-encoded URL list).

    Standard format: base64(url1\\nurl2\\n...). Compatible with v2rayNG,
    Hiddify, and other V2Ray clients that support subscription import.
    """
    urls: list[str] = []
    # Relay URLs first (recommended)
    if relay_entries:
        for relay_set in relay_entries:
            for purl in relay_set.urls:
                if purl.url:
                    urls.append(purl.url)
    # Direct URLs
    for p in protocol_urls:
        if p.url:
            urls.append(p.url)
    if not urls:
        return ""
    return base64.b64encode("\n".join(urls).encode()).decode()


def render_pwa_shell(
    *,
    client_name: str = "",
    asset_path: str = "../pwa",
    server_name: str = "",
) -> str:
    """Render the PWA HTML shell from the index.html.j2 template.

    The shell is lightweight — it loads config.json and shared assets
    at runtime.  Only the client_name, asset_path, and server_name
    are baked in.
    """
    return _render_pwa_template(
        "index.html.j2",
        client_name=client_name,
        asset_path=asset_path,
        server_name=server_name,
    )


def render_manifest(
    *,
    client_name: str = "",
    asset_path: str = "../pwa",
    server_name: str = "",
) -> str:
    """Render the per-client PWA manifest from manifest.webmanifest.j2."""
    return _render_pwa_template(
        "manifest.webmanifest.j2",
        client_name=client_name,
        asset_path=asset_path,
        server_name=server_name,
    )


def _render_pwa_template(
    filename: str,
    **variables: object,
) -> str:
    """Load and render a Jinja2 template from templates/pwa/."""
    try:
        from importlib.resources import files

        template_text = (files("meridian") / "templates" / "pwa" / filename).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Failed to load PWA template %s: %s", filename, exc)
        return ""

    from jinja2 import TemplateError

    # HTML templates get autoescape; JSON manifests do not
    use_autoescape = filename.endswith(".html.j2")
    env = _create_jinja_env(autoescape=use_autoescape)
    env.filters["capitalize"] = lambda v: str(v).capitalize()
    env.filters["tojson"] = lambda v: json.dumps(str(v), ensure_ascii=False)[1:-1]

    try:
        tmpl = env.from_string(template_text)
        return tmpl.render(**variables)
    except TemplateError as exc:
        logger.warning("Failed to render PWA template %s: %s", filename, exc)
        return ""


# ---------------------------------------------------------------------------
# Shared internal helpers
# ---------------------------------------------------------------------------


def _create_jinja_env(*, autoescape: bool = True) -> "jinja2.Environment":
    """Create a Jinja2 Environment with common filters.

    Both ``_render_template`` (connection-info) and ``_render_pwa_template``
    (PWA shell/manifest) share the ``default`` filter and BaseLoader.
    Callers add template-specific filters after creation.
    """
    from jinja2 import BaseLoader, Environment

    env = Environment(loader=BaseLoader(), autoescape=autoescape)

    def default_filter(value: object, default_value: object = "") -> object:
        if value is None or value == "":
            return default_value
        return value

    env.filters["default"] = default_filter
    return env


def _load_template_text() -> str | None:
    """Load the bundled Jinja2 connection-info template.

    Returns the template text or None if not available.
    """
    try:
        from importlib.resources import files

        template_path = files("meridian") / "templates" / "connection-info.html.j2"
        return template_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Failed to load connection-info template: %s", exc)
        return None


def _url_by_key(protocol_urls: list[ProtocolURL], key: str) -> str:
    """Get URL string for a protocol key, or empty string."""
    return next((p.url for p in protocol_urls if p.key == key), "")


def _qr_by_key(protocol_urls: list[ProtocolURL], key: str) -> str:
    """Get QR base64 for a protocol key, or empty string."""
    return next((p.qr_b64 for p in protocol_urls if p.key == key), "")


def _build_template_variables(
    *,
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str,
    now: str,
    is_server_hosted: bool,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
) -> dict[str, object]:
    """Build the Jinja2 template variable dict.

    QR base64 strings are passed directly to the template as plain strings.
    """
    reality_url = _url_by_key(protocol_urls, "reality")
    xhttp_url = _url_by_key(protocol_urls, "xhttp")
    wss_url = _url_by_key(protocol_urls, "wss")
    reality_qr = _qr_by_key(protocol_urls, "reality")
    xhttp_qr = _qr_by_key(protocol_urls, "xhttp")
    wss_qr = _qr_by_key(protocol_urls, "wss")

    variables: dict[str, object] = {
        "vless_reality_url": reality_url,
        "vless_xhttp_url": xhttp_url,
        "vless_wss_url": wss_url,
        "reality_qr_b64": reality_qr,
        "xhttp_qr_b64": xhttp_qr,
        "wss_qr_b64": wss_qr,
        "server_public_ip": server_ip,
        "domain": domain,
        "domain_mode": bool(domain),
        "xhttp_enabled": bool(xhttp_url),
        "is_server_hosted": is_server_hosted,
        "client_name": client_name,
        "generated_at": {"iso8601": now},
    }

    # Relay entries (if any)
    if relay_entries:
        relay_data = []
        for relay_set in relay_entries:
            relay_urls_data = []
            for purl in relay_set.urls:
                if purl.url:
                    qr = purl.qr_b64 or generate_qr_base64(purl.url)
                    relay_urls_data.append(
                        {
                            "key": purl.key,
                            "label": purl.label,
                            "url": purl.url,
                            "qr_b64": qr,
                        }
                    )
            if not relay_urls_data:
                continue  # skip relay entries with no valid URLs
            relay_data.append(
                {
                    "ip": relay_set.relay_ip,
                    "name": relay_set.relay_name,
                    "urls": relay_urls_data,
                    # Keep first URL for backwards compat with template
                    "url": relay_urls_data[0]["url"] if relay_urls_data else "",
                    "qr_b64": relay_urls_data[0]["qr_b64"] if relay_urls_data else "",
                }
            )
        variables["relays"] = relay_data
        variables["has_relays"] = True
    else:
        variables["relays"] = []
        variables["has_relays"] = False

    return variables


def _render_template(
    template_text: str | None,
    variables: dict[str, object],
    *,
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str,
    client_name: str,
    now: str,
) -> str:
    """Render the Jinja2 template with variables, falling back to minimal HTML.

    Args:
        template_text: The Jinja2 template source, or None if unavailable.
        variables: Template variable dict (from ``_build_template_variables``).
        protocol_urls: Protocol URLs with QR data (for fallback rendering).
        server_ip: Server IP (for fallback).
        domain: Domain (for fallback).
        client_name: Client name (for fallback).
        now: ISO 8601 timestamp (for fallback).
    """
    if template_text:
        try:
            from jinja2 import TemplateError
        except ImportError:
            TemplateError = Exception  # type: ignore[assignment,misc]

        try:
            env = _create_jinja_env(autoescape=True)
            env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")

            tmpl = env.from_string(template_text)
            return tmpl.render(**variables)
        except (TemplateError, ImportError, FileNotFoundError, OSError):
            pass

    # Fallback: minimal HTML — use protocol_urls directly
    qr_map = {p.key: p.qr_b64 for p in protocol_urls if p.qr_b64}

    # Extract relay entries from variables for fallback rendering
    relay_data_raw = variables.get("relays")
    fallback_relays: list[RelayURLSet] | None = None
    if relay_data_raw and isinstance(relay_data_raw, list):
        fallback_relays = []
        for rd in relay_data_raw:
            if isinstance(rd, dict) and rd.get("url"):
                fallback_relays.append(
                    RelayURLSet(
                        relay_ip=rd["ip"],
                        relay_name=rd.get("name", ""),
                        urls=[ProtocolURL(key="reality", label="Primary (via relay)", url=rd["url"])],
                    )
                )
    return _generate_minimal_html(
        client_name, protocol_urls, qr_map, server_ip, domain, now, relay_entries=fallback_relays
    )


def _generate_minimal_html(
    client_name: str,
    protocol_urls: list[ProtocolURL],
    qr_map: dict[str, str],
    server_ip: str,
    domain: str,
    now: str,
    relay_entries: list[RelayURLSet] | None = None,
) -> str:
    """Generate a minimal HTML page when the Jinja2 template is not available."""
    safe_name = html_mod.escape(client_name)
    safe_ip = html_mod.escape(server_ip)
    safe_now = html_mod.escape(now)

    cards = ""

    # Relay cards (if any)
    if relay_entries:
        for relay_set in relay_entries:
            relay_label = relay_set.relay_name or relay_set.relay_ip
            for purl in relay_set.urls:
                if purl.url:
                    relay_qr = generate_qr_base64(purl.url)
                    cards += _html_card(f"* Recommended: via {html_mod.escape(relay_label)}", purl.url, relay_qr)
        cards += '<div class="card"><h2 style="color:#e5a44e">Backup (direct)</h2></div>'

    cards += "".join(_html_card(purl.label, purl.url, qr_map.get(purl.key, "")) for purl in protocol_urls if purl.url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connection Setup - {safe_name}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0c0e14;color:#e8eaf0;margin:0;padding:24px 16px}}
.wrap{{max-width:460px;margin:0 auto}}
h1{{font-size:1.3rem;text-align:center;margin-bottom:4px}}
.sub{{text-align:center;color:#8b90a8;font-size:.82rem;margin-bottom:24px}}
.card{{background:#181c28;border:1px solid #282d3e;border-radius:14px;padding:20px;margin-bottom:14px}}
.card h2{{font-size:.9rem;color:#5b9cf5;margin-bottom:12px}}
.qr{{text-align:center;margin:12px 0}}
.qr img{{width:200px;height:200px;border-radius:8px;background:#fff;padding:6px}}
.url{{background:#12151e;border:1px solid #282d3e;border-radius:8px;padding:10px;
font-family:monospace;font-size:.65rem;word-break:break-all;color:#555a72;cursor:pointer}}
a.btn{{display:block;background:#5b9cf5;color:#fff;text-align:center;padding:13px;
border-radius:10px;text-decoration:none;font-weight:600;margin-bottom:12px}}
.foot{{text-align:center;color:#555a72;font-size:.7rem;margin-top:24px}}
.foot a{{color:#8b90a8;text-decoration:none}}
</style>
</head>
<body>
<div class="wrap">
<h1>Connection Setup</h1>
<p class="sub">{safe_name} &middot; {safe_ip}</p>
{cards}
<div class="card">
<h2>Not connecting?</h2>
<p style="font-size:.82rem;color:#8b90a8">
Make sure your device clock is set automatically, then try another connection card above.
</p>
<p style="font-size:.82rem;color:#8b90a8">
If nothing works, ask the server owner to run <code>meridian test {safe_ip}</code>.
</p>
</div>
<div class="foot">
<a href="https://getmeridian.org">Powered by Meridian</a> &middot; {safe_now}
</div>
</div>
<script>
document.querySelectorAll('.url').forEach(function(el){{
el.onclick=function(){{navigator.clipboard&&navigator.clipboard.writeText(el.textContent)}}}})
</script>
</body>
</html>"""


def _html_card(title: str, url: str, qr_b64: str) -> str:
    """Generate a single protocol card for the minimal HTML page."""
    safe_title = html_mod.escape(title)
    safe_url = html_mod.escape(url)
    safe_qr = html_mod.escape(qr_b64)
    qr_html = f'<div class="qr"><img src="data:image/png;base64,{safe_qr}" alt="QR"></div>' if qr_b64 else ""
    return f"""<div class="card">
<h2>{safe_title}</h2>
<a class="btn" href="{safe_url}">Open in App</a>
{qr_html}
<div class="url">{safe_url}</div>
</div>
"""
